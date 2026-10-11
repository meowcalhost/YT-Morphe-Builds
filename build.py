"""Auto build pipeline.

latest Morphe patches + patcher  ->  latest compatible YT / YT Music versions
(from the patches)  ->  download per-arch APKs from APKMirror  ->  patch each
->  collect per-arch patched APKs in dist/ with a manifest.

Run:  python build.py              # stable (recommended) versions
      python build.py --experimental   # include test versions (-x)
CI publishes dist/*.apk to a GitHub release.
"""
import asyncio
import json
import re
from pathlib import Path

import morphe
from apkmirror import arch_of, download_app, use_fast_loop

DIST = Path("dist")


def _tool_version(path, pat):
    m = re.search(pat, Path(path).name)
    return m.group(1) if m else "unknown"


async def build(experimental: bool = False) -> dict:
    jar, mpp = morphe.ensure_tools()
    versions = morphe.list_compatible(experimental=experimental)
    DIST.mkdir(parents=True, exist_ok=True)

    builds = []
    for pkg, app in morphe.PKG_TO_APP.items():
        version = versions.get(pkg)
        entry = {"app": app, "package": pkg, "version": version, "arches": []}
        if not version:
            entry["error"] = "no compatible version reported by patches"
            builds.append(entry)
            continue

        dl = await download_app(app, version, only_apk=True)
        for r in dl["variants"]:
            if not (r.get("ok") and r["file"].lower().endswith(".apk")):
                continue
            arch = arch_of(r["info"])
            # patch into dist/, then rename to a clean <app>-<version>-<arch>.apk
            patched = await asyncio.to_thread(morphe.patch_apk, r["file"], str(DIST))
            rec = {"arch": arch, "ok": patched["ok"]}
            if patched["ok"]:
                out = DIST / f"{app}-{version}-{arch}.apk"
                Path(patched["output"]).replace(out)
                rec["file"] = out.name
                rec["bytes"] = out.stat().st_size
            else:
                rec["returncode"] = patched.get("returncode")
            entry["arches"].append(rec)
        builds.append(entry)

    manifest = {
        "patches_version": _tool_version(mpp, r"patches-([\d.]+)\.mpp"),
        "patcher_version": _tool_version(jar, r"morphe-desktop-([\d.]+)-all\.jar"),
        "experimental": experimental,
        "builds": builds,
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    import sys
    # offline self-check for the tool-version parser
    assert _tool_version("patches-1.46.0.mpp", r"patches-([\d.]+)\.mpp") == "1.46.0"
    assert _tool_version("morphe-desktop-1.18.1-all.jar", r"morphe-desktop-([\d.]+)-all\.jar") == "1.18.1"

    use_fast_loop()
    print(json.dumps(asyncio.run(build(experimental="--experimental" in sys.argv)), indent=2))
