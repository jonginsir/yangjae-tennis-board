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
from .core import (KST, alert_text, changes, closed_again, compose, health, new_openings,
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
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("telegram: secrets 없음")
        return
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
    print("telegram:", r.status_code, r.json().get("description", "ok"))





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
    alert_courts = set(os.environ.get("ALERT_COURTS", "A,B,C").split(","))
    week = "월화수목금토일"
    lines = [f"{d[5:7]}/{d[8:]}({week[datetime.strptime(d, '%Y-%m-%d').weekday()]}) {c}코트 {h:02d}시"
             for d, c, h in new_openings(prev, data) if c in alert_courts]
    print("new openings:", lines)
    fmt = lambda d, c, h: f"{d[5:7]}/{d[8:]}({week[datetime.strptime(d, '%Y-%m-%d').weekday()]}) {c}코트 {h:02d}시"
    closed_lines = [fmt(*x) for x in closed_again(prev, data, alert_courts)]
    change_lines = changes(prev, data)
    text = compose(opened=newly_opened_months(prev, data, alert_courts), new_lines=lines,
                   closed_lines=closed_lines, change_lines=change_lines,
                   health_msg=health_message(prev, data, now), board_url=BOARD_URL, biz_url=BIZ_URL)
    print("closed:", closed_lines, "changes:", change_lines)
    if text:
        send(text)
    if os.environ.get("TELEGRAM_TEST") == "true":
        send("✅ 알림 연결 테스트 (아래는 예시예요. 실제 상황이 아니에요)\n\n" + compose(
            opened={}, new_lines=["12/03(수) B코트 19시"], closed_lines=["12/03(수) B코트 19시"],
            change_lines=["새로 올라온 상품: 2월 A코트(실내), 2월 B코트(실내), 2월 C코트(실내)"],
            health_msg="⚠️ 보드 이상\nC코트 3일치 확인 실패 (12-01~12-03)", board_url=BOARD_URL, biz_url=BIZ_URL))

    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fails = sum(isinstance(v, Exception) for v in schedules.values())
    print(f"items={len(meta)} fails={fails}")
    if fails == len(meta):
        sys.exit("전부 실패 — 네이버 차단 가능성")


if __name__ == "__main__":
    main()
