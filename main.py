"""APK downloader + Morphe patcher API. Run: uvicorn main:app

GET  /                           -> health
GET  /download/{app}/{version}   -> download all variants (app = youtube | youtube-music)
GET  /patch/{app}/{version}      -> download the APK + apply the latest Morphe patches
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI

import morphe
from apkmirror import arch_of, download_app, use_fast_loop

use_fast_loop()  # uvloop where supported, before the server loop starts

app = FastAPI(title="YTexp — APK downloader + Morphe patcher")

# ponytail: one stealth browser at a time — serial is plenty for an experiment.
_lock = asyncio.Lock()


@app.get("/")
def health():
    return {
        "ok": True,
        "apps": ["youtube", "youtube-music"],
        "usage": ["GET /download/{app}/{version}", "GET /patch/{app}/{version}"],
    }


@app.get("/download/{app_name}/{version}")
async def download(app_name: str, version: str, only_apk: bool = False):
    async with _lock:
        return await download_app(app_name, version, only_apk=only_apk)


@app.get("/patch/{app_name}/{version}")
async def patch(app_name: str, version: str):
    async with _lock:
        res = await download_app(app_name, version, only_apk=True)
    apks = [r for r in res["variants"] if r.get("ok") and r["file"].lower().endswith(".apk")]
    if not apks:
        return {
            "ok": False,
            "stage": "download",
            "detail": "no per-arch .apk variant for this version (bundles only?) — needs an apkm slice step",
            "download": res,
        }
    # one patched APK per architecture; serial — patching is RAM/CPU heavy, don't thrash
    patched = []
    for r in apks:
        out_dir = Path(r["file"]).parent / "patched"
        p = await asyncio.to_thread(morphe.patch_apk, r["file"], str(out_dir))
        patched.append({"arch": arch_of(r["info"]), **p})
    return {
        "ok": all(p["ok"] for p in patched),
        "app": app_name,
        "version": version,
        "count": len(patched),
        "patched": patched,
    }
