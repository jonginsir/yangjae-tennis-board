import json
import pathlib
from datetime import datetime

from collector.board import build, relevant_items
from collector.core import (BOOKED, KST, NOTYET, OFF, OPEN, PAST, alert_text, classify,
                            new_openings, newly_opened_months, open_at, parse_item)

FX = pathlib.Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 25, 1, 5, tzinfo=KST)  # 탐색 수집 시각


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def row(slots, date, lo=6, hi=21):
    sym = {OPEN: "O", BOOKED: "x", NOTYET: "n", OFF: ".", PAST: "p"}
    return "".join(sym[slots[str(h)]] for h in range(lo, hi + 1)) if date else ""


def test_parse_item():
    assert parse_item("11월 A코트(실내)") == {"month": 11, "court": "A", "indoor": True}
    assert parse_item("1월 3번코트(실외,인조잔디)") == {"month": 1, "court": "3", "indoor": False}
    assert parse_item("공지사항") is None


def test_open_rule():
    assert open_at(2026, 12) == datetime(2026, 11, 1, 9, tzinfo=KST)
    assert open_at(2027, 1) == datetime(2026, 12, 1, 9, tzinfo=KST)


def test_matches_naver_screen_11_25_A():
    """GPT 보드/네이버 화면과 같은 칸: 11/25 A코트 06~10 마감, 11~13 운영 외, 14~21 마감."""
    data = build(_meta(["8073288"]), {"8073288": load("sched_11A.json")}, NOW)
    assert row(data["dates"]["2026-11-25"]["A"]["slots"], 1) == "xxxxx...xxxxxxxx"
    assert row(data["dates"]["2026-11-28"]["A"]["slots"], 1) == "x" * 16


def test_next_month_before_open_is_notyet_not_open():
    """1월분처럼 아직 오픈 전인 달은 예약 0건이라도 빈자리로 보면 안 된다."""
    slot = {"unitStartTime": "2027-01-05 10:00:00", "isUnitBusinessDay": True,
            "isUnitSaleDay": True, "isSaleDay": True, "unitBookingCount": 0, "unitStock": 1}
    assert classify(slot, NOW, open_at(2027, 1)) == NOTYET
    after = datetime(2026, 12, 1, 9, 1, tzinfo=KST)
    assert classify(slot, after, open_at(2027, 1)) == OPEN


def test_off_hours_beats_everything():
    slot = {"unitStartTime": "2026-12-03 06:00:00", "isUnitBusinessDay": False,
            "isUnitSaleDay": True, "isSaleDay": True, "unitBookingCount": 0, "unitStock": 1}
    assert classify(slot, NOW, open_at(2026, 12)) == OFF


def test_sale_stopped_is_booked():
    slot = {"unitStartTime": "2026-11-26 10:00:00", "isUnitBusinessDay": True,
            "isUnitSaleDay": False, "isSaleDay": True, "unitBookingCount": 0, "unitStock": 1}
    assert classify(slot, NOW, open_at(2026, 11)) == BOOKED


def test_failure_is_error_not_booked():
    """SPEC 3번: 확인 실패를 마감으로 표시하면 불합격."""
    data = build(_meta(["8152852"]), {"8152852": RuntimeError("HTTP 403")}, NOW)
    cell = data["dates"]["2026-12-01"]["A"]
    assert "error" in cell and "slots" not in cell
    assert len([d for d in data["dates"] if d.startswith("2026-12")]) == 31


def test_new_openings_only_changed_cells():
    prev = {"dates": {"2026-12-02": {"A": {"slots": {"6": BOOKED, "7": OPEN}}},
                      "2026-12-03": {"A": {"error": "x"}}}}
    cur = {"dates": {"2026-12-02": {"A": {"slots": {"6": OPEN, "7": OPEN}}},
                     "2026-12-03": {"A": {"slots": {"9": OPEN}}}}}
    assert new_openings(prev, cur) == [("2026-12-02", "A", 6)]
    assert new_openings(None, cur) == []


def _meta(ids):
    items = [i for i in load("items.json") if str(i["bizItemId"]) in ids]
    return relevant_items(items, NOW)


def test_month_opening_is_one_summary_not_flood():
    """1월분이 열리는 순간(오픈 전 -> 가능) 수백 칸을 하나씩 알리면 텔레그램 길이 제한에 걸린다."""
    prev = {"dates": {f"2027-01-{d:02d}": {"A": {"slots": {str(h): NOTYET for h in range(6, 22)}}} for d in range(1, 32)}}
    cur = {"dates": {f"2027-01-{d:02d}": {"A": {"slots": {str(h): OPEN for h in range(6, 22)}}} for d in range(1, 32)}}
    assert new_openings(prev, cur) == []
    assert newly_opened_months(prev, cur, {"A"}) == {("2027", "01"): 31 * 16}
    text = alert_text([], newly_opened_months(prev, cur), "B", "N")
    assert "1월 예약이 열렸어요" in text and len(text) < 4096


def test_alert_text_is_capped():
    lines = [f"12/0{i % 9 + 1} A코트 {h:02d}시" for i in range(40) for h in range(3)]
    text = alert_text(lines, {}, "B", "N")
    assert len(text) < 4096 and "외 95칸 더 있어요" in text
    assert alert_text([], {}, "B", "N") is None


def test_missing_day_in_successful_response_is_closed_not_error():
    """네이버가 정상 응답했는데 특정 날짜(예: C코트 월·금)에 칸이 없으면 '운영 안 함'이지 '확인 실패'가 아니다."""
    slots = [x for x in load("sched_11A.json") if not x["unitStartTime"].startswith("2026-11-27")]
    data = build(_meta(["8073288"]), {"8073288": slots}, NOW)
    cell = data["dates"]["2026-11-27"]["A"]
    assert cell.get("closedDay") and cell["slots"] == {} and "error" not in cell
