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
                before = pcell["slots"].get(hour)
                if st == OPEN and before not in (OPEN, NOTYET, None):
                    out.append((date, court, int(hour)))
    return sorted(out)


def newly_opened_months(prev: dict | None, cur: dict, courts=None):
    """'오픈 전' 칸이 이번에 처음 열린 달 -> {('2027','01'): 빈자리 수}. 칸별 알림 대신 한 줄 요약용."""
    if not prev:
        return {}
    out = {}
    for date, cs in cur.get("dates", {}).items():
        for court, cell in cs.items():
            if courts and court not in courts:
                continue
            pslots = (prev.get("dates", {}).get(date, {}).get(court) or {}).get("slots") or {}
            for hour, st in (cell.get("slots") or {}).items():
                if pslots.get(hour) == NOTYET and st != NOTYET:
                    key = (date[:4], date[5:7])
                    out.setdefault(key, 0)
                    out[key] += st == OPEN
    return out


def alert_text(lines, opened, board_url, biz_url, limit=25):
    """텔레그램 한 통(4096자 제한) 안에 들어가게 만든다."""
    parts = []
    for (y, m), n in sorted(opened.items()):
        parts.append(f"📅 {int(m)}월 예약이 열렸어요. 실내 빈자리 {n}칸")
    if lines:
        parts.append("🎾 양재 실내 빈자리 생김")
        parts += lines[:limit]
        if len(lines) > limit:
            parts.append(f"외 {len(lines) - limit}칸 더 있어요")
    if not parts:
        return None
    return "\n".join(parts) + f"\n\n보드: {board_url}\n예약: {biz_url}"
