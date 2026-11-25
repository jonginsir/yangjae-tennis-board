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


def alert_text(lines, opened, board_url, biz_url, limit=25):  # noqa: 이전 버전 호환
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


# ── 변경 감지 (보드 이상 / 다시 찬 자리 / 네이버 변경) ─────────────────
import html as _html

_META = re.compile(r'<meta[^>]+(?:name|property)="(?:og:)?description"[^>]*content="([^"]*)"', re.S)
_OPEN = re.compile(r"(\d{1,2})월분\s*코트\s*예약\s*:?\s*(\d{1,2})/(\d{1,2})\s*(\d{1,2})시\s*오픈")


def notice_from_html(page: str) -> str:
    """description 메타가 여러 개다(네이버 공통 문구 + 업체 공지). 가장 긴 것이 업체 공지."""
    found = [_html.unescape(x).strip() for x in _META.findall(page or "")]
    return max(found, key=len) if found else ""


def parse_open_notice(text: str) -> dict:
    """'12월분 코트예약 : 11/1 09시 오픈' -> {12: (11, 1, 9)}"""
    return {int(a): (int(b), int(c), int(d)) for a, b, c, d in _OPEN.findall(text or "")}


def closed_again(prev: dict | None, cur: dict, courts=None):
    """직전엔 빈자리였는데 이제 마감된 칸 (시간이 지나서 사라진 건 제외)."""
    if not prev:
        return []
    out = []
    for date, cs in cur.get("dates", {}).items():
        for court, cell in cs.items():
            if courts and court not in courts:
                continue
            pslots = (prev.get("dates", {}).get(date, {}).get(court) or {}).get("slots") or {}
            for hour, st in (cell.get("slots") or {}).items():
                if pslots.get(hour) == OPEN and st == BOOKED:
                    out.append((date, court, int(hour)))
    return sorted(out)


def health(cur: dict, courts=None) -> dict:
    """보드 상태: 확인 실패 칸이 있으면 이상."""
    bad = {}
    for date, cs in cur.get("dates", {}).items():
        for court, cell in cs.items():
            if (courts is None or court in courts) and "error" in cell:
                bad.setdefault(court, []).append(date)
    issues = [f"{c}코트 {len(ds)}일치 확인 실패 ({ds[0][5:]}~{ds[-1][5:]})" if c.isalpha()
              else f"{c}번 코트 {len(ds)}일치 확인 실패" for c, ds in sorted(bad.items())]
    return {"ok": not issues, "issues": issues}


def changes(prev: dict | None, cur: dict) -> list[str]:
    """네이버 쪽 변화: 상품 목록, 공지."""
    if not prev:
        return []
    out = []
    pl, cl = set(prev.get("listed") or []), set(cur.get("listed") or [])
    if prev.get("listed") is not None:
        if cl - pl:
            out.append("새로 올라온 상품: " + ", ".join(sorted(cl - pl)))
        if pl - cl:
            out.append("목록에서 내려간 상품(보드는 계속 추적): " + ", ".join(sorted(pl - cl)))
    if prev.get("notice") and cur.get("notice") and prev["notice"] != cur["notice"]:
        out.append("공지가 바뀌었어요:\n" + cur["notice"][:350] + ("…" if len(cur["notice"]) > 350 else ""))
    return out


def compose(*, opened, new_lines, closed_lines, change_lines, health_msg, board_url, biz_url, limit=25):
    """한 번의 수집에서 보낼 텔레그램 메시지 한 통 (4096자 이내)."""
    parts = []
    for (y, m), n in sorted(opened.items()):
        parts.append(f"📅 {int(m)}월 예약이 열렸어요. 실내 빈자리 {n}칸")
    if new_lines:
        parts += ["🎾 양재 실내 빈자리 생김"] + new_lines[:limit]
        if len(new_lines) > limit:
            parts.append(f"외 {len(new_lines) - limit}칸 더 있어요")
    if closed_lines:
        parts += ["🔒 다시 마감됐어요"] + closed_lines[:15]
        if len(closed_lines) > 15:
            parts.append(f"외 {len(closed_lines) - 15}칸")
    if change_lines:
        parts += ["📢 네이버 변경"] + change_lines
    if health_msg:
        parts.append(health_msg)
    if not parts:
        return None
    text = "\n".join(parts)
    tail = f"\n\n보드: {board_url}\n예약: {biz_url}"
    return text[: 4096 - len(tail)] + tail
