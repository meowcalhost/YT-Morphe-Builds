# YT-Morphe-Builds

Automated per-architecture [Morphe](https://github.com/MorpheApp)-patched builds of
**YouTube** and **YouTube Music**. A scheduled GitHub Action watches for new Morphe
patch releases, resolves the latest compatible app version, downloads the per-arch
APKs from APKMirror, patches each, and publishes them to a GitHub Release.

## How it works

```
latest morphe-patches (.mpp) + morphe-desktop (patcher jar)
      -> list-versions  ->  latest compatible YT / YT Music version
      -> APKMirror       ->  per-arch APKs (arm64-v8a, armeabi-v7a, x86, x86_64)
      -> morphe patch    ->  one signed patched APK per architecture
      -> GitHub Release  ->  dist/<app>-<version>-<arch>.apk
```

- **Per-architecture, not one bulk APK.** APKMirror serves YouTube Music as separate
  per-arch APKs, so each is downloaded and patched independently.
- **Downloader** uses [Camoufox](https://github.com/daijro/camoufox) (stealth Firefox)
  to avoid bot blocking; the browser performs the download so APKMirror's `download.php`
  gets the headers it expects.
- **Latest compatible version** comes from the patches themselves
  (`morphe-desktop list-versions`), not a hardcoded list.

## Modules

| File | Role |
|------|------|
| `apkmirror.py` | Download every variant of an APKMirror app version (`download_app`). |
| `morphe.py` | Fetch latest patcher + patches, list compatible versions, patch an APK. |
| `build.py` | The pipeline: resolve versions -> download -> patch -> `dist/`. |
| `main.py` | Optional FastAPI server (`/download/...`, `/patch/...`). |
| `.github/workflows/build.yml` | Scheduled auto-build + release. |

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and a JRE 21+.

```bash
uv sync
uv run camoufox fetch          # one-time browser download (~1.3 GB)
uv run python build.py         # builds dist/<app>-<version>-<arch>.apk + manifest.json
```

Serve the API instead:

```bash
uv run uvicorn main:app        # GET /patch/youtube-music/9.20.53
```

## Notes / limitations

- **uvloop** is used on Linux/macOS; Windows falls back to the default asyncio loop
  (uvloop has no Windows build and winloop can't spawn Playwright's driver).
- **YouTube** on APKMirror is published as a *universal* APK (not per-arch), so it
  patches to a single universal APK. YouTube Music is genuinely per-arch.
- **CI from datacenter IPs**: APKMirror may challenge GitHub-runner IPs more
  aggressively. If downloads start failing in Actions, route the browser through a
  residential proxy.
- Builds are for personal use; patches and the patcher are Morphe's.
