"""수집 결과를 보드용 data.json 구조로 만든다 (네트워크 없음)."""
from __future__ import annotations

from datetime import datetime, timedelta

from .core import ERROR, KST, classify, item_year, month_range, open_at, parse_item

BIZ_URL = "https://m.booking.naver.com/booking/10/bizes/210031"


def merge_items(listed: list[dict], seed: list[dict], prev: dict | None) -> list[dict]:
    """네이버 목록은 마감된 달 상품을 숨기기도 한다. 목록 + 알려진 상품(seed) + 직전 수집에서 본 상품을 합친다."""
    out = {str(i["bizItemId"]): {**i, "listed": True} for i in listed}
    for i in seed:
        out.setdefault(str(i["bizItemId"]), {**i, "listed": False})
    for date, courts in (prev or {}).get("dates", {}).items():
        for court, cell in courts.items():
            iid = cell.get("itemId")
            if iid and iid not in out:
                kind = "실내" if court.isalpha() else "실외"
                out[iid] = {"bizItemId": iid, "name": f"{int(date[5:7])}월 {court}코트({kind})", "listed": False}
    return list(out.values())


def relevant_items(items: list[dict], now: datetime):
    """이번 달 이후 상품만, (연, 월, 코트) 정보와 함께."""
    out = []
    for it in items:
        p = parse_item(it.get("name", ""))
        if not p:
            continue
        year = item_year(p["month"], now)
        start, end = month_range(year, p["month"])
        if end < now:
            continue
        out.append({**p, "year": year, "id": str(it["bizItemId"]), "name": it["name"], "listed": it.get("listed", True),
                    "start": max(start, now.replace(hour=0, minute=0, second=0, microsecond=0)),
                    "end": end, "opens": open_at(year, p["month"])})
    return out


def court_order(c):
    return (0 if c["indoor"] else 1, c["id"].zfill(3))


def build(items_meta: list[dict], schedules: dict, now: datetime) -> dict:
    """schedules: {item_id: [slot, ...]} 또는 {item_id: Exception}"""
    courts = {}
    dates: dict = {}
    for it in items_meta:
        courts[it["court"]] = {"id": it["court"], "indoor": it["indoor"],
                               "label": f"{it['court']}코트" if it["indoor"] else f"{it['court']}번 코트"}
        res = schedules.get(it["id"])
        checked = now.isoformat(timespec="seconds")
        if isinstance(res, Exception) or res is None:
            d = it["start"]
            while d <= it["end"]:
                dates.setdefault(d.strftime("%Y-%m-%d"), {})[it["court"]] = {
                    "error": str(res) if res else "응답 없음", "checkedAt": checked, "itemId": it["id"]}
                d += timedelta(days=1)
            continue
        for slot in res:
            date, hour = slot["unitStartTime"][:10], int(slot["unitStartTime"][11:13])
            cell = dates.setdefault(date, {}).setdefault(
                it["court"], {"slots": {}, "checkedAt": checked, "itemId": it["id"],
                              "notyetUntil": it["opens"].isoformat(timespec="minutes")})
            cell["slots"][str(hour)] = classify(slot, now, it["opens"])
        # 조회는 성공했는데 그 날짜 칸이 아예 없으면: 예약을 받지 않는 날(조회 실패 아님)
        d = it["start"]
        while d <= it["end"]:
            key = d.strftime("%Y-%m-%d")
            dates.setdefault(key, {}).setdefault(it["court"], {
                "slots": {}, "closedDay": True, "checkedAt": checked, "itemId": it["id"]})
            d += timedelta(days=1)
    return {
        "generatedAt": now.isoformat(timespec="seconds"),
        "bizUrl": BIZ_URL,
        "courts": sorted(courts.values(), key=court_order),
        "dates": dict(sorted(dates.items())),
        "states": ["open", "booked", "notyet", "off", ERROR, "past"],
    }

