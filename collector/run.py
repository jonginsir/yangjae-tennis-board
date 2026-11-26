"""수집 실행: 네이버 예약에서 읽기만 한다(예약·로그인 없음). 결과를 site/data.json에 쓴다."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import datetime

import requests

from .board import BIZ_URL, build, merge_items, relevant_items
from .core import (KST, alert_text, changes, closed_again, compose, health, new_openings, wants,
                   newly_opened_months, notice_from_html, parse_open_notice)

GQL = "https://m.booking.naver.com/graphql"
HERE = pathlib.Path(__file__).parent
SITE = pathlib.Path(os.environ.get("SITE_DIR", "site"))
PREV_URL = os.environ.get("PREV_URL", "")  # 직전 data.json (Pages 주소)
GAP = float(os.environ.get("REQUEST_GAP", "1.0"))  # 요청 사이 간격(초)

ITEMS_Q = "query bizItems($input: BizItemsParams) { bizItems(input: $input) { bizItemId name } }"
SCHED_Q = (HERE / "hourlySchedule.graphql").read_text()

S = requests.Session()
S.headers.update({
    "content-type": "application/json",
    "referer": BIZ_URL,
    "origin": "https://m.booking.naver.com",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "accept-language": "ko-KR,ko;q=0.9",
})


def gql(op, query, variables):
    r = S.post(f"{GQL}?opName={op}", json={"operationName": op, "query": query, "variables": variables},
               timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(str(body["errors"])[:200])
    return body["data"]


def fetch_schedule(item):
    data = gql("hourlySchedule", SCHED_Q, {"scheduleParams": {
        "businessTypeId": 10, "businessId": "210031", "bizItemId": item["id"],
        "startDateTime": item["start"].strftime("%Y-%m-%dT00:00:00"),
        "endDateTime": item["end"].strftime("%Y-%m-%dT23:59:59"),
        "fixedTime": True, "includesHolidaySchedules": True}})
    return data["schedule"]["bizItemSchedule"]["hourly"] or []


BOARD_URL = "https://jonginsir.github.io/yangjae-tennis-board/"


def send(text):
    """텔레그램 전송. 실패해도 수집·배포는 계속되도록 예외를 밖으로 내보내지 않는다(3번 재시도)."""
    tok = os.environ.get("TELEGRAM_TOKEN")
    chats = chat_ids(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not (tok and chats):
        print("telegram: secrets 없음 또는 채팅 ID 숫자를 못 찾음")
        return False
    print("telegram 대상:", len(chats), "개", [f"{c[:4]}…({len(c)}자리)" for c in chats])  # 공개 로그라 일부만
    return all([_send_one(tok, c, text) or (not c.startswith("-") and _send_one(tok, "-" + c, text, hint=True))
                for c in chats])


def chat_ids(raw: str):
    """Secrets 값에서 채팅 ID 숫자만 뽑는다. 메시지 전체를 붙여넣었거나 여러 개(쉼표·줄바꿈)여도 된다.
    유니코드 마이너스(−, –)도 '-'로 본다."""
    import re
    raw = re.sub("[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]", "-", raw or "")  # 여러 모양의 대시를 '-'로
    out = []
    for c in re.findall(r"-?\d{5,}", raw):
        if not c.startswith("-") and len(c) == 13 and c.startswith("100"):
            c = "-" + c  # 슈퍼그룹 ID(-100 + 10자리)에서 '-'만 빠진 경우
        out.append(c)
    return list(dict.fromkeys(out))


def _send_one(tok, chat, text, hint=False):
    if hint:
        print("telegram: 앞에 '-'를 붙여 다시 시도 (그룹 ID는 '-'로 시작해요)")
    for attempt in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=20)
            body = r.json()
            print("telegram:", r.status_code, body.get("description", "ok"))
            new_id = (body.get("parameters") or {}).get("migrate_to_chat_id")
            if new_id and str(new_id) != str(chat):
                # 그룹이 슈퍼그룹으로 바뀌면 ID가 -100... 으로 바뀐다. 텔레그램이 알려준 새 ID로 다시 보낸다.
                print("telegram: 슈퍼그룹 전환됨 → 새 ID로 재전송. Secrets를 새 ID로 바꾸면 이 단계가 사라져요")
                return _send_one(tok, str(new_id), text)
            if r.status_code < 500:
                return r.status_code == 200
        except Exception as e:
            print("telegram 연결 실패:", type(e).__name__)
        time.sleep(3 * (attempt + 1))
    return False





def current_open(data, courts):
    return sorted((d, c, int(h)) for d, cs in data["dates"].items() for c, cell in cs.items()
                  if c in courts for h, st in (cell.get("slots") or {}).items() if st == "open")


def compose_lines(lines):
    from .core import _line
    return [_line(x) for x in lines[:25]]


def debounce_listing(meta, prev):
    """네이버 목록이 수집마다 깜빡인다(11/25 확인). 이번과 직전 둘 다 목록에 없을 때만 '내려간 상품'으로 본다."""
    prev_listed = set((prev or {}).get("listed") or [])
    for m in meta:
        if not m["listed"] and m["name"] in prev_listed:
            m["listed"] = True
    return meta


def health_message(prev, data, now):
    """보드 이상이 생기거나 바뀌면 알리고, 복구되면 한 번 알린다."""
    cur, ph = data["health"], (prev or {}).get("health")
    msgs = []
    if not cur["ok"] and (ph is None or ph.get("ok") or ph.get("issues") != cur["issues"]):
        msgs.append("⚠️ 보드 이상\n" + "\n".join(cur["issues"][:10]))
    if cur["ok"] and ph and not ph.get("ok"):
        msgs.append("✅ 보드가 정상으로 돌아왔어요")
    if prev and prev.get("generatedAt"):
        gap = (now - datetime.fromisoformat(prev["generatedAt"])).total_seconds() / 60
        if gap > 40:
            msgs.append(f"✅ 수집이 다시 돌아왔어요 (약 {int(gap)}분 멈춰 있었어요)")
    return "\n".join(msgs) or None


def _dedupe(meta):
    """같은 (연, 월, 코트)가 둘 이상이면 네이버 목록에 있는 상품을 우선한다."""
    best = {}
    for m in meta:
        k = (m["year"], m["month"], m["court"])
        if k not in best or (m["listed"] and not best[k]["listed"]):
            best[k] = m
    return list(best.values())


def main():
    now = datetime.now(KST)
    prev = None
    if PREV_URL:
        try:
            prev = requests.get(PREV_URL, timeout=15).json()
        except Exception as e:
            print("prev data 없음:", e)
    listed = gql("bizItems", ITEMS_Q, {"input": {"businessId": "210031", "lang": "ko"}})["bizItems"]
    seed = json.loads((HERE / "seed_items.json").read_text(encoding="utf-8"))
    notice = ""
    try:
        pr = S.get(BIZ_URL, headers={"accept": "text/html,application/xhtml+xml"}, timeout=20)
        notice = notice_from_html(pr.text)
        print("biz page:", pr.status_code, len(pr.text), "description" in pr.text, "월분" in pr.text, pr.url[:80])
    except Exception as e:
        print("공지 읽기 실패:", e)
    open_notice = parse_open_notice(notice)
    print("open notice:", open_notice)
    meta = relevant_items(merge_items(listed, seed, prev), now, open_notice)
    meta = _dedupe(meta)
    debounce_listing(meta, prev)
    print("listed:", len(listed), "unlisted used:", [m["name"] for m in meta if not m["listed"]])
    if not meta:
        sys.exit("상품 목록을 해석하지 못함 — 이름 형식이 바뀌었는지 확인 필요")
    schedules = {}
    for it in meta:
        try:
            schedules[it["id"]] = fetch_schedule(it)
        except Exception as e:  # 칸을 '확인 실패'로 남긴다 (마감으로 표시하지 않음)
            schedules[it["id"]] = e
            print("fail", it["name"], e)
        time.sleep(GAP)
    data = build(meta, schedules, now)
    data["listed"] = sorted(i["name"] for i in listed)
    data["known"] = sorted(set((prev or {}).get("known") or []) | {m["name"] for m in meta} | set(data["listed"]))
    data["notice"] = notice
    data["health"] = health(data)
    rules = json.loads((HERE / "alert_rules.json").read_text(encoding="utf-8"))
    keep = lambda d, c, h: wants(d, c, h, rules)
    week = "월화수목금토일"

    def label(d, c, h):
        court = f"{c}코트" if c.isalpha() else f"{c}번 코트"
        return f"{d[5:7]}/{d[8:]}({week[datetime.strptime(d, '%Y-%m-%d').weekday()]}) {court} {h:02d}시"

    def slot_link(d, c, h):
        iid = data["dates"][d][c].get("itemId")
        return (label(d, c, h), f"{BIZ_URL}/items/{iid}?startDate={d}") if iid else label(d, c, h)

    opened_all = [x for x in new_openings(prev, data) if keep(*x)]
    lines = [slot_link(*x) for x in opened_all if x[1].isalpha()]
    out_lines = [slot_link(*x) for x in opened_all if not x[1].isalpha()]
    print("new openings:", len(lines), "indoor,", len(out_lines), "outdoor(in window)")
    closed_lines = [label(*x) for x in closed_again(prev, data) if keep(*x)]  # 마감된 칸은 링크 불필요
    change_lines = changes(prev, data)
    indoor_ids = {c["id"] for c in data["courts"] if c["indoor"]}
    outdoor_ids = {c["id"] for c in data["courts"] if not c["indoor"]}
    text = compose(opened=newly_opened_months(prev, data, indoor_ids, keep),
                   opened_out=newly_opened_months(prev, data, outdoor_ids, keep),
                   new_lines=lines, out_new_lines=out_lines,
                   closed_lines=closed_lines, change_lines=change_lines,
                   health_msg=health_message(prev, data, now), board_url=BOARD_URL, biz_url=BIZ_URL)
    print("closed:", len(closed_lines), "changes:", change_lines)
    if text:
        send(text)
    if os.environ.get("SEND_CURRENT") == "true":
        cur = [x for x in current_open(data, indoor_ids | outdoor_ids) if keep(*x)]
        ins = compose_lines([slot_link(*x) for x in cur if x[1].isalpha()])
        outs = compose_lines([slot_link(*x) for x in cur if not x[1].isalpha()])
        send("🎾 <b>지금 예약 가능한 실내 자리</b>\n" + ("\n".join(ins) if ins else "지금은 없어요")
             + "\n\n🌳 <b>지금 예약 가능한 실외 자리 (내 시간대)</b>\n" + ("\n".join(outs) if outs else "지금은 없어요")
             + f'\n\n<a href="{BOARD_URL}">보드 열기</a>')
    if os.environ.get("TELEGRAM_TEST") == "true":
        send("📢 <b>양재 테니스장 빈자리 알림방</b>\n\n"
             "15분마다 네이버 예약을 확인해서 이 방으로 알려드려요.\n\n"
             "🎾 실내 A·B·C에 새 빈자리가 생기면 → 줄을 누르면 그 날짜 예약 화면이 열려요\n"
             "🌳 실외는 화 20시, 수 17~20시, 토·일 8~19시에 시작하는 칸만\n"
             "🔒 알린 빈자리가 다시 차면\n"
             "📅 다음 달 예약이 열리면 (공지 기준 시각)\n"
             "📢 네이버 공지나 상품 구성이 바뀌면\n"
             "⚠️ 보드에 문제가 생기거나 복구되면\n\n"
             "예약은 각자 네이버에서 직접 해주세요. 먼저 누르는 사람이 가져가요.\n\n"
             f'<a href="{BOARD_URL}">빈자리 보드 열기</a>  ·  <a href="{BIZ_URL}">네이버 예약</a>')

    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fails = sum(isinstance(v, Exception) for v in schedules.values())
    print(f"items={len(meta)} fails={fails}")
    if fails == len(meta):
        sys.exit("전부 실패 — 네이버 차단 가능성")


if __name__ == "__main__":
    main()
