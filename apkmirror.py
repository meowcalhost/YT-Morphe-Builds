"""Download every architecture variant of an APKMirror app version (YouTube,
YouTube Music, ...).

Camoufox (stealth Firefox) drives the click-through and the browser itself does
the download (captured via expect_download), so APKMirror's download.php sees the
headers it wants. ponytail: one browser, variants fetched serially.
"""
import asyncio
import re
from pathlib import Path

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import TimeoutError as PWTimeoutError

BASE = "https://www.apkmirror.com"
VENDOR = "google-inc"  # APKMirror path segment: /apk/google-inc/<app>/


def use_fast_loop() -> str:
    """Install uvloop as the asyncio policy where it's supported (Linux/macOS);
    asyncio.run()/Lock keep working on top of it. ponytail: Windows has no uvloop
    build, and winloop (its port) can't spawn Playwright's driver subprocess
    ('startupinfo is not supported'), so we stay on the default asyncio loop there."""
    import sys
    if sys.platform == "win32":
        return "asyncio"
    try:
        import uvloop
        uvloop.install()
        return "uvloop"
    except ImportError:
        return "asyncio"


def version_slug(app: str, version: str) -> str:
    """('youtube','19.47.41') -> 'youtube-19-47-41-release' (APKMirror release-page slug)."""
    return f"{app}-" + re.sub(r"[.\s]+", "-", version.strip().lower()) + "-release"


ARCHES = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86", "universal")


def arch_of(info: str) -> str:
    """Pick the architecture token out of an APKMirror variant's row text."""
    for a in ARCHES:
        if a in info:
            return a
    return "unknown"


async def _goto(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    try:  # best-effort settle for any bot/Cloudflare interstitial
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass


async def _list_variants(page, app, version):
    url = f"{BASE}/apk/{VENDOR}/{app}/{version_slug(app, version)}/"
    await _goto(page, url)
    variants = []
    rows = page.locator("div.table-row")
    for i in range(await rows.count()):
        row = rows.nth(i)
        link = row.locator("a.accent_color").first
        if await link.count() == 0:
            continue
        href = await link.get_attribute("href") or ""
        if "-android-apk-download/" not in href:
            continue
        info = " ".join((await row.inner_text()).split())
        variants.append({
            "url": href if href.startswith("http") else BASE + href,
            "info": info,
        })
    return url, variants


async def _download_variant(context, variant_url, dest, idx):
    """variant page -> 'Download' button -> download page, then let Firefox download
    the file and capture it. In-browser (not context.request) so download.php gets the
    headers/UA it expects and serves the file instead of 403."""
    page = await context.new_page()
    try:
        await _goto(page, variant_url)
        btn = page.locator("a.downloadButton").first
        await btn.wait_for(timeout=30000)
        href = await btn.get_attribute("href") or ""
        dl_page = href if href.startswith("http") else BASE + href

        try:  # the download page auto-starts the download a couple seconds after load
            async with page.expect_download(timeout=60000) as di:
                await page.goto(dl_page, wait_until="domcontentloaded", timeout=90000)
            dl = await di.value
        except PWTimeoutError:  # fall back to clicking the explicit "here" link
            async with page.expect_download(timeout=180000) as di:
                await page.locator("a[href*='download.php']").first.click()
            dl = await di.value

        name = dl.suggested_filename or f"variant_{idx:02d}.apk"
        out = dest / f"{idx:02d}_{name}"
        await dl.save_as(str(out))
        return out
    finally:
        await page.close()


async def download_app(app: str, version: str, dest_root: str = "downloads", only_apk: bool = False) -> dict:
    """app = 'youtube' | 'youtube-music' (any APKMirror google-inc slug).
    only_apk=True skips BUNDLE (.apkm/.apks) rows — handy when you just need a
    single installable APK (e.g. to feed a patcher)."""
    dest = Path(dest_root) / app / version
    dest.mkdir(parents=True, exist_ok=True)
    results = []

    async with AsyncCamoufox(headless=True) as browser:
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        list_url, variants = await _list_variants(page, app, version)
        await page.close()

        if only_apk:  # ponytail: the row text carries the "BUNDLE" badge for apkm/apks
            variants = [v for v in variants if "BUNDLE" not in v["info"].upper()]

        for idx, v in enumerate(variants):
            try:
                out = await _download_variant(context, v["url"], dest, idx)
                results.append({**v, "ok": True, "file": str(out), "bytes": out.stat().st_size})
            except Exception as e:  # keep going; one bad variant shouldn't kill the run
                results.append({**v, "ok": False, "error": repr(e)})

        await context.close()

    return {
        "app": app,
        "version": version,
        "release_page": list_url,
        "found": len(variants),
        "downloaded": sum(1 for r in results if r.get("ok")),
        "dest": str(dest),
        "variants": results,
    }


if __name__ == "__main__":
    # offline self-check for the pure logic
    assert version_slug("youtube", "19.47.41") == "youtube-19-47-41-release"
    assert version_slug("youtube-music", " 9.40.51 ") == "youtube-music-9-40-51-release"
    print("ok")

    import sys
    if len(sys.argv) > 2:  # manual live run: python apkmirror.py youtube-music 9.40.51
        import json
        use_fast_loop()
        print(json.dumps(asyncio.run(download_app(sys.argv[1], sys.argv[2])), indent=2))
