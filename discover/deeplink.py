"""탐색: 상품 상세 주소에 날짜 파라미터를 붙이면 그 날짜가 선택되는지 확인 (읽기만, 클릭 없음)."""
import json, pathlib
from playwright.sync_api import sync_playwright
OUT = pathlib.Path("discover-out"); OUT.mkdir(exist_ok=True)
BASE = "https://m.booking.naver.com/booking/10/bizes/210031/items/8152852"  # 12월 A코트
CASES = {"none": BASE, "startDate": BASE + "?startDate=2026-12-17", "date": BASE + "?date=2026-12-17",
         "startDateTime": BASE + "?startDateTime=2026-12-17T19:00:00"}
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 390, "height": 844},
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    for name, url in CASES.items():
        page = ctx.new_page(); vars_ = []
        page.on("request", lambda r: vars_.append(json.loads(r.post_data or "{}").get("variables")) if "hourlySchedule" in r.url and r.post_data else None)
        page.goto(url, wait_until="networkidle", timeout=60000); page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / f"deeplink_{name}.png"), full_page=False)
        res[name] = {"final": page.url, "sched_vars": vars_[:3], "snippet": page.inner_text("body")[:600]}
        page.close()
    b.close()
(OUT / "deeplink.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for k, v in res.items(): print(k, v["final"], v["sched_vars"][:1])
