"""ASX probe 7: drive the announcements page in a real browser and capture
the API traffic it generates - the JS-site equivalent of reading a
server-rendered page. Reveals the exact request shape (params/headers/any
anonymous token) the PUBLIC page uses to page through announcement history.

Run in CI with Playwright + Chromium.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/probe/asx7")
OUT.mkdir(parents=True, exist_ok=True)

captured: list[dict] = []
responses: dict[str, dict] = {}


def interesting(url: str) -> bool:
    return bool(re.search(r"markitdigital|asx-research|announcement", url, re.I)) \
        and not re.search(r"\.(js|css|png|svg|woff)", url)


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120 Safari/537.36"))
        page = ctx.new_page()

        def on_request(req):
            if interesting(req.url):
                captured.append({
                    "url": req.url[:500], "method": req.method,
                    "headers": {k: v for k, v in req.headers.items()
                                if k.lower() in ("x-api-key", "authorization", "referer",
                                                 "origin", "x-mkt-api-key", "apikey")
                                or k.lower().startswith("x-")},
                })

        def on_response(resp):
            if interesting(resp.url) and "announcement" in resp.url.lower():
                try:
                    body = resp.text()[:3000]
                except Exception:  # noqa: BLE001
                    body = "<unreadable>"
                responses[resp.url[:300]] = {"status": resp.status, "body_head": body}

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto("https://www.asx.com.au/markets/trade-our-cash-market/announcements.uwc",
                  wait_until="networkidle", timeout=90_000)
        page.screenshot(path=str(OUT / "page1.png"))

        # search for AFI in whatever search box exists
        try:
            box = page.locator("input[type='search'], input[placeholder*='ompan'], "
                               "input[placeholder*='ode']").first
            box.fill("AFI")
            page.wait_for_timeout(2500)
            page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
            # click a suggestion if a dropdown appeared
            sug = page.locator("li, [role='option']").filter(has_text=re.compile("AFI", re.I)).first
            if sug.count():
                sug.click()
                page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001
            print("search interaction:", exc)
        page.screenshot(path=str(OUT / "page2.png"))

        # try paging: click anything that looks like next/more/older
        for _ in range(3):
            try:
                btn = page.locator("button, a").filter(
                    has_text=re.compile(r"next|more|older|load", re.I)).first
                if btn.count() and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(3000)
                else:
                    break
            except Exception:  # noqa: BLE001
                break
        page.screenshot(path=str(OUT / "page3.png"))
        browser.close()

    (OUT / "requests.json").write_text(json.dumps(captured, indent=1))
    (OUT / "responses.json").write_text(json.dumps(responses, indent=1))
    ann_calls = [c for c in captured if "announcement" in c["url"].lower()]
    print(f"captured {len(captured)} interesting requests, {len(ann_calls)} announcement calls")
    for c in ann_calls[:10]:
        print(" ", c["method"], c["url"][:200])
        print("   headers:", c["headers"])
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
