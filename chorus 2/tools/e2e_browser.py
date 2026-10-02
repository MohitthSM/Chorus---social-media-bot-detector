"""Load the real extension in Chromium against a fixture X page, click a badge, screenshot it.

Needs: pip install playwright && playwright install chromium
Run a seeded backend first:
    cd backend && CHORUS_DB_PATH=/tmp/e2e.db chorus-backend &
    python scripts/simulate.py --url http://127.0.0.1:8000
Then, from the repo root:
    python tools/e2e_browser.py --out e2e.png
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="e2e.png")
    ap.add_argument("--profile", default="/tmp/chorus-e2e-profile")
    a = ap.parse_args()

    clusters = json.loads(urllib.request.urlopen(a.backend + "/v1/clusters/x").read())["clusters"]
    if not clusters:
        raise SystemExit("no clusters; seed the backend with scripts/simulate.py first")
    member = max(clusters, key=lambda c: c["stats"]["mean_score"])["members"][0]

    html = (EXT / "test/fixtures/x_thread.html").read_text().replace("abcde12345678", member)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            a.profile, channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
        if not ctx.service_workers:
            ctx.wait_for_event("serviceworker", timeout=10000)
        ctx.route("https://x.com/**",
                  lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
        page = ctx.new_page()
        page.goto("https://x.com/Pol_A/status/100")
        badge = f'.chorus-badge[data-handle="{member}"]'
        page.wait_for_selector(badge, timeout=15000)
        page.click(badge)
        page.wait_for_selector(".chorus-panel")
        print(page.inner_text(".chorus-panel"))
        time.sleep(0.5)
        page.screenshot(path=a.out, full_page=True)
        ctx.close()
    print("screenshot:", a.out)


if __name__ == "__main__":
    main()
