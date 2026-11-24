"""순수 로직: 네트워크 없이 테스트할 수 있는 부분만 둔다."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 칸 상태 (SPEC.md 2번)
OPEN, BOOKED, NOTYET, OFF, ERROR, PAST = "open", "booked", "notyet", "off", "error", "past"

_NAME = re.compile(r"^\s*(\d{1,2})월\s*(.+?)코트\s*\((.+)\)")


def parse_item(name: str):
    """'11월 A코트(실내)' -> {'month': 11, 'court': 'A', 'indoor': True}. 형식이 다르면 None."""
    m = _NAME.match(name or "")
    if not m:
        return None
    month, court, kind = int(m.group(1)), m.group(2).strip(), m.group(3)
    court = court.rstrip("번")
    return {"month": month, "court": court, "indoor": "실내" in kind}


def item_year(month: int, today: datetime) -> int:
    """상품 이름에는 연도가 없다. 오늘 기준 6개월 넘게 과거인 달이면 내년으로 본다."""
    return today.year + 1 if month < today.month - 6 else today.year


def month_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=KST)
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=KST)
    return start, nxt - timedelta(seconds=1)


def open_at(year: int, month: int, day: int = 1, hour: int = 9) -> datetime:
    """공지 기준: 'N월분 예약은 전달 1일 09시 오픈'. 공지가 바뀌면 config에서 조정."""
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    return datetime(py, pm, day, hour, tzinfo=KST)


def slot_start(slot) -> datetime:
    return datetime.strptime(slot["unitStartTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)


def classify(slot: dict, now: datetime, opens: datetime) -> str:
    if not slot.get("isUnitBusinessDay"):
        return OFF
    if slot_start(slot) <= now:
        return PAST
    if now < opens:
        return NOTYET
    if not slot.get("isUnitSaleDay") or not slot.get("isSaleDay"):
        return BOOKED  # 판매 중지 칸: 예약 불가이므로 마감으로 본다
    if (slot.get("unitBookingCount") or 0) >= (slot.get("unitStock") or 1):
        return BOOKED
    return OPEN


def new_openings(prev: dict | None, cur: dict):
    """직전 수집에서는 빈자리가 아니었는데 지금 빈자리인 칸만 돌려준다 (SPEC 7번).
    직전 칸이 확인 실패였거나 직전 데이터가 없으면 알리지 않는다(실패 후 복구 때 폭탄 알림 방지)."""
    if not prev:
        return []
    out = []
    for date, courts in cur.get("dates", {}).items():
        for court, cell in courts.items():
            slots = cell.get("slots") or {}
            pcell = prev.get("dates", {}).get(date, {}).get(court)
            if not pcell or "slots" not in pcell:
                continue
            for hour, st in slots.items():
                if st == OPEN and pcell["slots"].get(hour) not in (OPEN, None):
                    out.append((date, court, int(hour)))
    return sorted(out)
