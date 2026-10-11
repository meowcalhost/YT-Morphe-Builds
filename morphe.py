"""Fetch the latest Morphe patches + patcher and apply them to an APK.

- patches:  github.com/MorpheApp/morphe-patches   -> patches-*.mpp
- patcher:  github.com/MorpheApp/morphe-desktop    -> morphe-desktop-*-all.jar  (CLI; needs Java 21+)
  (morphe-patcher is only a library, so the runnable CLI is morphe-desktop.)

CLI per the docs:  java -jar morphe-desktop-*-all.jar patch -p patches-*.mpp app.apk
                   -> writes app-patched.apk next to the input.
ponytail: stdlib only (urllib/subprocess), tools cached under tools/.
"""
import glob
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

TOOLS = Path("tools")
RELEASES = {
    "jar": ("https://api.github.com/repos/MorpheApp/morphe-desktop/releases/latest", "-all.jar"),
    "mpp": ("https://api.github.com/repos/MorpheApp/morphe-patches/releases/latest", ".mpp"),
}


def _gh_headers() -> dict:
    """GitHub API headers; add the token when present so CI calls hit the 5000/hr
    authenticated limit instead of the 60/hr-per-IP anonymous one."""
    h = {"User-Agent": "ytexp", "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _latest_asset(api_url: str, suffix: str):
    """(asset_name, download_url) of the newest release asset ending in `suffix`."""
    req = urllib.request.Request(api_url, headers=_gh_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    for a in data.get("assets", []):
        if a["name"].endswith(suffix):
            return a["name"], a["browser_download_url"]
    raise RuntimeError(f"no asset ending {suffix} in {api_url} (tag {data.get('tag_name')})")


def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "ytexp"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest


def ensure_tools() -> tuple[Path, Path]:
    """Return (patcher_jar, patches_mpp), downloading the latest of each if absent.
    Falls back to any cached copy if GitHub is unreachable."""
    out = {}
    for key, (api, suffix) in RELEASES.items():
        try:
            name, url = _latest_asset(api, suffix)
            path = TOOLS / name
            if not path.exists():
                _download(url, path)
            out[key] = path
        except Exception:  # offline: use whatever we cached before
            cached = sorted(TOOLS.glob(f"*{suffix}"))
            if not cached:
                raise
            out[key] = cached[-1]
    return out["jar"], out["mpp"]


def find_java() -> str:
    """A Java 21+ launcher. Morphe needs JRE 21+, and the PATH `java` may be older."""
    if os.environ.get("MORPHE_JAVA"):
        return os.environ["MORPHE_JAVA"]
    pats = [
        r"C:\Program Files\Microsoft\jdk-2*\bin\java.exe",
        r"C:\Program Files\Java\jdk-2*\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\jdk-2*\bin\java.exe",
    ]
    cands = sorted(c for p in pats for c in glob.glob(p))
    # jdk-21/22/23/24... sort lexically-ish; take the highest that is >= 21
    for c in reversed(cands):
        if any(f"jdk-{v}" in c for v in range(21, 40)):
            return c
    return "java"  # last resort; errors clearly if < 21


def _keystore_args() -> list[str]:
    """Sign every build with a persisted keystore when MORPHE_KEYSTORE points at one.
    Without it morphe generates a throwaway key per run, so each release is signed
    with a different key and Android rejects an in-place update ("App not installed")
    until the old build is uninstalled. Alias/passwords default to how morphe makes
    its own keystore (alias "Morphe", empty passwords); override via env only if yours
    differs."""
    ks = os.environ.get("MORPHE_KEYSTORE")
    if not ks or not Path(ks).is_file():
        return []
    args = [f"--keystore={ks}",
            f"--keystore-entry-alias={os.environ.get('MORPHE_KEYSTORE_ALIAS', 'Morphe')}"]
    for env, flag in (("MORPHE_KEYSTORE_PASSWORD", "--keystore-password"),
                      ("MORPHE_KEYSTORE_ENTRY_PASSWORD", "--keystore-entry-password")):
        v = os.environ.get(env)
        if v is not None:
            args.append(f"{flag}={v}")
    return args


def patch_apk(input_apk, out_dir=None, java=None) -> dict:
    """Apply the latest Morphe patches to a single APK. Returns {ok, output, log}.
    Morphe writes <stem>-patched.apk next to the input and signs it itself."""
    jar, mpp = ensure_tools()
    java = java or find_java()
    src = Path(input_apk)
    if not src.is_file():
        raise FileNotFoundError(src)

    cmd = [java, "-jar", str(jar), "patch", *_keystore_args(), "-p", str(mpp), str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    full = proc.stdout + proc.stderr
    log = full[-4000:]

    # Morphe prints the real output path ("Saved to ...") — name/layout varies by
    # version (it nests under a per-input subfolder), so trust the log over a guess.
    m = re.search(r"Saved to (.+?\.apk)", full)
    produced = Path(m.group(1).strip()) if m else src.with_name(f"{src.stem}-patched{src.suffix}")
    if proc.returncode != 0 or not produced.exists():
        return {"ok": False, "input": str(src), "returncode": proc.returncode, "log": log}

    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / produced.name
        produced.replace(target)
        try:  # drop the now-empty subfolder Morphe created next to the input
            produced.parent.rmdir()
        except OSError:
            pass
        produced = target
    return {"ok": True, "input": str(src), "output": str(produced), "log": log}


PKG_TO_APP = {  # Morphe package name -> APKMirror google-inc slug
    "com.google.android.youtube": "youtube",
    "com.google.android.apps.youtube.music": "youtube-music",
}


def _parse_versions(text: str) -> dict:
    """Pull {package: newest-listed-version} out of `list-versions` output.
    The first version under each 'Package name:' header is the top (most patches,
    newest) = the latest version the patches fully support."""
    res = {}
    for block in re.split(r"Package name:\s*", text)[1:]:
        pkg = block.splitlines()[0].strip()
        m = re.search(r"(\d+\.\d+\.\d+)", block)
        if m:
            res[pkg] = m.group(1)
    return res


def list_compatible(packages=None, experimental=False, java=None) -> dict:
    """Run morphe `list-versions` against the current patches and return
    {package_name: latest_compatible_version}. experimental=True adds -x (test versions)."""
    jar, mpp = ensure_tools()
    java = java or find_java()
    cmd = [java, "-jar", str(jar), "list-versions", f"--patches={mpp}"]
    if experimental:
        cmd.append("-x")
    for p in (packages or PKG_TO_APP):
        cmd.append(f"-f={p}")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return _parse_versions(out.stdout + out.stderr)


if __name__ == "__main__":
    import sys
    # offline self-check for the version parser
    sample = ("Package name: com.google.android.apps.youtube.music\n"
              "Most common compatible versions:\n\t9.20.53 (47 patches)\n\t9.15.51 (47 patches)\n\n"
              "Package name: com.google.android.youtube\n"
              "Most common compatible versions:\n\t21.16.256 (92 patches)\n")
    assert _parse_versions(sample) == {
        "com.google.android.apps.youtube.music": "9.20.53",
        "com.google.android.youtube": "21.16.256",
    }

    # keystore args: off without the env, full flag set with it
    os.environ.pop("MORPHE_KEYSTORE", None)
    assert _keystore_args() == []
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".keystore", delete=False) as tf:
        os.environ["MORPHE_KEYSTORE"] = tf.name
    assert _keystore_args() == [f"--keystore={tf.name}", "--keystore-entry-alias=Morphe"]
    os.environ.pop("MORPHE_KEYSTORE", None)
    os.unlink(tf.name)

    if len(sys.argv) > 1:  # manual: python morphe.py path\to\app.apk
        print(json.dumps(patch_apk(sys.argv[1]), indent=2))
    else:
        j, m = ensure_tools()
        print(f"patcher: {j}\npatches: {m}\njava:    {find_java()}")
        print("compatible:", json.dumps(list_compatible(), indent=2))
