"""Can we use londonstockexchange.com, and for what?

This probe answers the PERMISSION question before the capability one. It
does not scrape and does not build a dataset. It fetches robots.txt, the
terms/legal pages it can find, and ONE stock page, then reports what is
allowed and what is actually served.

Why the order matters. The site's own recent-trades and price data is the
kind of market data exchanges license commercially, and the standing rule
for this project is that licensing restrictions and access controls are
never bypassed. So the probe is written to STOP being useful the moment a
control appears: if robots.txt disallows the path, or the page comes back
as a bot-challenge rather than content, that is the finding - not an
obstacle to route around.

Writes outputs/probe/lse_probe.json.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/3s)")
BASE = "https://www.londonstockexchange.com"
# one live fund we already hold, and one of the seven whose ticker we
# suspect is stale after a rename
SAMPLES = ["CTY", "SLPE"]
OUT = Path("data/probe/lse/lse_probe.json")
THROTTLE = 3.0

# a bot-challenge page is not content, however healthy its status code
CHALLENGE = re.compile(
    r"(incapsula|imperva|akamai|distil|cf-browser-verification|"
    r"enable javascript|captcha|are you a robot|_Incapsula_Resource)", re.I)


def get(s: requests.Session, url: str) -> dict:
    time.sleep(THROTTLE)
    try:
        r = s.get(url, timeout=45, headers={"User-Agent": UA})
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}
    body = r.text or ""
    return {
        "url": url,
        "status": r.status_code,
        "bytes": len(r.content),
        "content_type": r.headers.get("Content-Type", ""),
        "server": r.headers.get("Server", ""),
        "looks_like_challenge": bool(CHALLENGE.search(body[:20000])),
        "head": body[:400],
    }


def main() -> int:
    out: dict = {"base": BASE, "user_agent": UA}
    s = requests.Session()

    rb = get(s, f"{BASE}/robots.txt")
    out["robots"] = rb
    allowed: dict[str, bool | None] = {}
    if rb.get("status") == 200 and not rb.get("looks_like_challenge"):
        # record the file verbatim so the decision is auditable, not my summary
        out["robots_text"] = rb["head"]
        rp = RobotFileParser()
        rp.parse(rb["head"].splitlines())
        try:
            full = requests.get(f"{BASE}/robots.txt", timeout=45,
                                headers={"User-Agent": UA}).text
            out["robots_text"] = full[:8000]
            rp = RobotFileParser()
            rp.parse(full.splitlines())
        except Exception:  # noqa: BLE001
            pass
        for code in SAMPLES:
            path = f"/stock/{code}/x/company-page"
            allowed[path] = rp.can_fetch(UA, BASE + path)
        allowed["/stock/"] = rp.can_fetch(UA, f"{BASE}/stock/")
    out["robots_allows"] = allowed

    # Only touch a stock page if robots.txt permits it. An empty `allowed`
    # means robots.txt could not be read, which is itself a reason to stop.
    if allowed and all(v for v in allowed.values() if v is not None):
        pg = get(s, f"{BASE}/stock/{SAMPLES[0]}/x/company-page")
        # The first run showed a 200 with a generic <title> and an Angular
        # shell, which SUGGESTS the markup carries no data - but suggests is
        # not knows. Look for the things we would actually need, so the
        # conclusion is a measurement instead of my reading of a snippet.
        body = ""
        try:
            body = requests.get(f"{BASE}/stock/{SAMPLES[0]}/x/company-page",
                                timeout=45, headers={"User-Agent": UA}).text
        except Exception:  # noqa: BLE001
            pass
        low = body.lower()
        pg["contains"] = {
            "fund_name_city_of_london": "city of london" in low,
            "ticker_CTY": "cty" in low,
            "any_price_pattern": bool(re.search(r"\b\d{2,4}\.\d{1,2}\b", body)),
            "words_recent_trades": "recent trades" in low,
            # where does the page say its data comes from?
            "references_lsecws": "lsecws" in low,
            "script_tags": low.count("<script"),
            "total_bytes": len(body),
        }
        out["pages"] = [pg]
    else:
        out["pages"] = []
        out["skipped_pages"] = "robots.txt does not permit these paths"

    # the terms are the licensing question; fetch the pointer, do not judge
    out["terms_candidates"] = [
        f"{BASE}/terms-and-conditions", f"{BASE}/legal", f"{BASE}/disclaimer"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("robots_text",)}, indent=2)[:4000])
    print("\n--- robots.txt (verbatim, first 2000 chars) ---")
    print(out.get("robots_text", "<unreadable>")[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
