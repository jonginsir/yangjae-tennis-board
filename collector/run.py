"""수집 실행: 네이버 예약에서 읽기만 한다(예약·로그인 없음). 결과를 site/data.json에 쓴다."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import datetime

import requests

from .board import BIZ_URL, build, relevant_items
from .core import KST, alert_text, new_openings, newly_opened_months

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





def main():
    now = datetime.now(KST)
    items = gql("bizItems", ITEMS_Q, {"input": {"businessId": "210031", "lang": "ko"}})["bizItems"]
    meta = relevant_items(items, now)
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

    prev = None
    if PREV_URL:
        try:
            prev = requests.get(PREV_URL, timeout=15).json()
        except Exception as e:
            print("prev data 없음:", e)
    alert_courts = set(os.environ.get("ALERT_COURTS", "A,B,C").split(","))
    week = "월화수목금토일"
    lines = [f"{d[5:7]}/{d[8:]}({week[datetime.strptime(d, '%Y-%m-%d').weekday()]}) {c}코트 {h:02d}시"
             for d, c, h in new_openings(prev, data) if c in alert_courts]
    print("new openings:", lines)
    text = alert_text(lines, newly_opened_months(prev, data, alert_courts), BOARD_URL, BIZ_URL)
    if text:
        send(text)
    if os.environ.get("TELEGRAM_TEST") == "true":
        send("✅ 알림 연결 테스트 (아래는 예시예요)\n\n" + alert_text(["12/03(수) B코트 19시", "12/03(수) B코트 20시"], {}, BOARD_URL, BIZ_URL))

    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fails = sum(isinstance(v, Exception) for v in schedules.values())
    print(f"items={len(meta)} fails={fails}")
    if fails == len(meta):
        sys.exit("전부 실패 — 네이버 차단 가능성")


if __name__ == "__main__":
    main()
