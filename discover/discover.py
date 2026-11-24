"""탐색용: 네이버 예약 페이지를 읽기만 하고, 페이지가 받아오는 데이터 응답을 기록한다.
클릭은 링크 이동(상품 상세 열기)만 한다. 예약·로그인 동작은 하지 않는다."""
import json, pathlib, sys
from playwright.sync_api import sync_playwright

BIZ_URL = "https://m.booking.naver.com/booking/10/bizes/210031"
OUT = pathlib.Path("discover-out")
OUT.mkdir(exist_ok=True)
captured = []


def on_response(resp):
    ct = resp.headers.get("content-type", "")
    if "json" not in ct:
        return
    try:
        body = resp.text()
    except Exception as e:  # noqa
        body = f"<<read error {e}>>"
    req = resp.request
    captured.append({
        "url": req.url, "method": req.method, "status": resp.status,
        "post": (req.post_data or "")[:4000], "body": body[:200000],
    })


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(locale="ko-KR", timezone_id="Asia/Seoul",
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                        viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.on("response", on_response)
    r = page.goto(BIZ_URL, wait_until="networkidle", timeout=60000)
    print("biz status", r.status if r else None)
    page.screenshot(path=str(OUT / "biz.png"), full_page=True)
    (OUT / "biz.html").write_text(page.content(), encoding="utf-8")
    page.wait_for_timeout(3000)
    links = sorted({a for a in page.eval_on_selector_all("a", "els => els.map(e => e.href)") if "/items/" in a})
    (OUT / "biz.txt").write_text(page.inner_text("body"), encoding="utf-8")
    (OUT / "item_links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1))
    print("item links", len(links))
    for i, link in enumerate(links[:6]):
        page.wait_for_timeout(2000)
        rr = page.goto(link, wait_until="networkidle", timeout=60000)
        print("item", i, rr.status if rr else None, link)
        page.screenshot(path=str(OUT / f"item{i}.png"), full_page=True)
    b.close()

(OUT / "responses.json").write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
print("json responses", len(captured))
sys.exit(0)
