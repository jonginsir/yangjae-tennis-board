import json
import pathlib
from datetime import datetime

from collector.board import build, merge_items, relevant_items
from collector.core import (changes, closed_again, compose, health, notice_from_html, parse_open_notice,
                            BOOKED, KST, NOTYET, OFF, OPEN, PAST, alert_text, classify,
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


def test_unlisted_items_are_still_tracked():
    """네이버 목록에서 사라진(마감된 달) 상품도 seed/직전 데이터로 계속 조회한다."""
    listed = [i for i in load("items.json") if not i["name"].startswith(("11월 A", "12월 A"))]
    merged = merge_items(listed, load("items.json"), None)
    names = {m["name"] for m in merged}
    assert "11월 A코트(실내)" in names and "12월 A코트(실내)" in names
    prev = {"dates": {"2026-12-03": {"B": {"itemId": "999"}}}}
    merged2 = merge_items([], [], prev)
    assert merged2 == [{"bizItemId": "999", "name": "12월 B코트(실내)", "listed": False}]


NOTICE_HTML = """<meta data-react-helmet="true" name="description" property="description" content="* 공 지 *12월분 코트예약 : 11/1 09시 오픈(오픈시간 예고없이 변경될수 있음) * 한시적 1인 1일 4시간">"""


def test_notice_parsing_and_open_time_override():
    text = notice_from_html(NOTICE_HTML)
    assert text.startswith("* 공 지") and "12월분" in text
    assert parse_open_notice(text) == {12: (11, 1, 9)}
    assert parse_open_notice("1월분 코트예약 : 12/2 10시 오픈") == {1: (12, 2, 10)}
    # 공지에 1월분이 12/2 10시로 나오면 그 시각을 쓴다(연도 넘어감 처리)
    items = [{"bizItemId": "1", "name": "1월 A코트(실내)"}]
    m = relevant_items(items, NOW, {1: (12, 2, 10)})[0]
    assert m["opens"] == datetime(2026, 12, 2, 10, tzinfo=KST)
    assert relevant_items(items, NOW, {})[0]["opens"] == datetime(2026, 12, 1, 9, tzinfo=KST)


def test_closed_again_excludes_time_passing():
    prev = {"dates": {"2026-12-02": {"A": {"slots": {"19": OPEN, "20": OPEN, "21": OPEN}}}}}
    cur = {"dates": {"2026-12-02": {"A": {"slots": {"19": BOOKED, "20": PAST, "21": OPEN}}}}}
    assert closed_again(prev, cur, {"A"}) == [("2026-12-02", "A", 19)]


def test_health_flags_error_cells():
    cur = {"dates": {"2026-12-01": {"A": {"error": "x"}}, "2026-12-02": {"A": {"error": "x"}, "B": {"slots": {}}}}}
    h = health(cur)
    assert not h["ok"] and h["issues"] == ["A코트 2일치 확인 실패 (12-01~12-02)"]
    assert health({"dates": {"2026-12-01": {"A": {"slots": {}}}}})["ok"]


def test_changes_detects_listing_and_notice():
    prev = {"listed": ["12월 A코트(실내)"], "known": ["12월 A코트(실내)", "11월 A코트(실내)"], "notice": "12월분 11/1 09시"}
    cur = {"listed": ["12월 A코트(실내)", "11월 A코트(실내)", "2월 A코트(실내)"], "notice": "1월분 12/1 09시"}
    out = changes(prev, cur)
    assert any(x == "새로 올라온 상품: 2월 A코트(실내)" for x in out)   # 11월 A는 다시 보인 것뿐 → 알리지 않음
    assert any(x.startswith("공지가 바뀌었어요") for x in out)
    assert changes({"notice": "a"}, {"notice": "a", "listed": []}) == []  # 직전에 목록 기록이 없으면 비교 안 함


def test_compose_fits_one_message():
    t = compose(opened={}, new_lines=["x"] * 100, closed_lines=["y"] * 50, change_lines=["공지가 바뀌었어요:\n" + "가" * 2000],
                health_msg="⚠️ 보드 이상", board_url="B", biz_url="N")
    assert len(t) <= 4096 and t.endswith('<a href="N">네이버 예약</a>')
    assert compose(opened={}, new_lines=[], closed_lines=[], change_lines=[], health_msg=None, board_url="B", biz_url="N") is None


def test_health_message_only_on_transitions():
    from collector.run import health_message
    bad = {"health": {"ok": False, "issues": ["A코트 3일치 확인 실패"]}, "generatedAt": NOW.isoformat()}
    good = {"health": {"ok": True, "issues": []}, "generatedAt": NOW.isoformat()}
    assert "보드 이상" in health_message(good, bad, NOW)
    assert health_message(bad, bad, NOW) is None          # 같은 이상이 계속되면 반복 알림 안 함
    assert "정상으로" in health_message(bad, good, NOW)
    assert health_message(good, good, NOW) is None
    from datetime import timedelta
    old = {**good, "generatedAt": (NOW - timedelta(minutes=90)).isoformat()}
    assert "약 90분 멈춰" in health_message(old, good, NOW)


def test_real_page_notice_is_business_notice_not_naver_boilerplate():
    page = ('<meta name="description" content="네이버 예약이 연동된 곳 어디서나 바로 예약하고">'
            + NOTICE_HTML)
    assert "12월분" in notice_from_html(page)


def test_unlisted_item_never_shows_open():
    """11/25: 목록에서 내려간 11월 A코트의 취소 칸이 '빈자리'로 보였지만 네이버에서 예약 화면이 안 열렸다."""
    meta = _meta(["8073288"])
    meta[0]["listed"] = False
    slot = {"unitStartTime": "2026-11-26 18:00:00", "isUnitBusinessDay": True, "isUnitSaleDay": True,
            "isSaleDay": True, "unitBookingCount": 0, "unitStock": 1}
    data = build(meta, {"8073288": [slot]}, NOW)
    assert data["dates"]["2026-11-26"]["A"]["slots"]["18"] == BOOKED
    meta[0]["listed"] = True
    assert build(meta, {"8073288": [slot]}, NOW)["dates"]["2026-11-26"]["A"]["slots"]["18"] == OPEN


def test_listing_flicker_needs_two_runs():
    from collector.run import debounce_listing
    meta = [{"name": "11월 A코트(실내)", "listed": False}]
    assert debounce_listing(meta, {"listed": ["11월 A코트(실내)"]})[0]["listed"] is True   # 한 번 빠짐 → 무시
    meta = [{"name": "11월 A코트(실내)", "listed": False}]
    assert debounce_listing(meta, {"listed": []})[0]["listed"] is False                   # 두 번 연속 → 내려간 상품


def test_alert_lines_link_to_exact_item_and_date_and_escape():
    t = compose(opened={}, new_lines=[("12/17(목) A코트 19시", "https://m.booking.naver.com/booking/10/bizes/210031/items/8152852?startDate=2026-12-17")],
                closed_lines=[], change_lines=["공지가 바뀌었어요:\n<b>주의</b> & 매크로"], health_msg=None, board_url="B", biz_url="N")
    assert '<a href="https://m.booking.naver.com/booking/10/bizes/210031/items/8152852?startDate=2026-12-17">12/17(목) A코트 19시 ›</a>' in t
    assert "&lt;b&gt;주의&lt;/b&gt; &amp; 매크로" in t    # 공지 속 기호가 텔레그램 HTML을 깨지 않게
    long = compose(opened={}, new_lines=[("x" * 50, "https://e.com/" + "y" * 80)] * 200, closed_lines=[], change_lines=[],
                   health_msg=None, board_url="B", biz_url="N")
    assert len(long) <= 4096 and long.count("<a ") == long.count("</a>")   # 링크 태그가 잘리지 않음


def test_telegram_failure_never_breaks_collection(monkeypatch):
    """11/25 23:12: 텔레그램 연결이 끊기자 수집 전체가 실패했다. 전송 실패는 삼켜야 한다."""
    import collector.run as run
    monkeypatch.setenv("TELEGRAM_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1")
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    def boom(*a, **k):
        raise run.requests.ConnectionError("reset")
    monkeypatch.setattr(run.requests, "post", boom)
    assert run.send("x") is False
