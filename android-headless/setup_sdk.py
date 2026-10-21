#!/usr/bin/env python3
"""Bootstrap a headless Android SDK + emulator AVD without Android Studio.

Downloads the Google command-line tools and platform-tools, accepts licences,
installs the emulator and an x86_64 system image, then creates an AVD.

Everything lands under ANDROID_HOME (default %LOCALAPPDATA%\\Android\\Sdk on Windows),
so removing that directory fully undoes the install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO_XML = "https://dl.google.com/android/repository/repository2-3.xml"
PLATFORM_TOOLS_ZIP = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"

API = os.environ.get("ANDROID_API", "34")
ABI = os.environ.get("ANDROID_ABI", "x86_64")
IMAGE = f"system-images;android-{API};google_apis;{ABI}"
AVD_NAME = os.environ.get("AVD_NAME", "headless")
IS_WINDOWS = os.name == "nt"


def sdk_root() -> Path:
    env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if env:
        return Path(env).expanduser()
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Android" / "Sdk"
    return Path.home() / "Android" / "Sdk"


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"[setup] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def require_java() -> None:
    """sdkmanager and avdmanager are Java programs; a missing JDK is the usual first failure."""
    java = shutil.which("java")
    if not java:
        die(
            "no `java` on PATH. Install a JDK 17+ (e.g. Temurin or Microsoft OpenJDK) and reopen "
            "your shell. Android's SDK tools will not run without one."
        )
    out = subprocess.run([java, "-version"], capture_output=True, text=True)
    log(f"java: {(out.stderr or out.stdout).strip().splitlines()[0]}")


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    log(f"  -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def latest_cmdline_tools_url() -> str:
    """Read the SDK repository manifest so we never pin a stale tools build."""
    log("resolving latest command-line tools from repository manifest")
    with urllib.request.urlopen(REPO_XML) as resp:
        tree = ET.parse(resp)
    best: tuple[int, str] | None = None
    for pkg in tree.iter("remotePackage"):
        if pkg.get("path") != "cmdline-tools;latest":
            continue
        for archive in pkg.iter("archive"):
            host_os = archive.findtext("host-os")
            if host_os and host_os != ("windows" if IS_WINDOWS else "linux"):
                continue
            url = archive.findtext("complete/url")
            if not url:
                continue
            # Prefer the highest revision we see.
            rev = int(pkg.findtext("revision/major") or 0)
            if best is None or rev >= best[0]:
                best = (rev, url)
    if not best:
        die("could not find cmdline-tools in the repository manifest; download manually from "
            "https://developer.android.com/studio#command-tools")
    return best[1]


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


def install_cmdline_tools(root: Path, tmp: Path) -> None:
    """sdkmanager only works from a directory literally named `latest`."""
    target = root / "cmdline-tools" / "latest"
    if (target / "bin").exists():
        log("cmdline-tools already installed")
        return
    zip_path = download(latest_cmdline_tools_url(), tmp / "cmdline-tools.zip")
    staging = tmp / "cmdline-tools-extract"
    if staging.exists():
        shutil.rmtree(staging)
    extract_zip(zip_path, staging)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging / "cmdline-tools"), str(target))
    log(f"cmdline-tools -> {target}")


def install_platform_tools(root: Path, tmp: Path) -> None:
    if (root / "platform-tools" / ("adb.exe" if IS_WINDOWS else "adb")).exists():
        log("platform-tools already installed")
        return
    zip_path = download(PLATFORM_TOOLS_ZIP, tmp / "platform-tools.zip")
    extract_zip(zip_path, root)
    log(f"platform-tools -> {root / 'platform-tools'}")


def sdk_tool(root: Path, name: str) -> Path:
    suffix = ".bat" if IS_WINDOWS else ""
    return root / "cmdline-tools" / "latest" / "bin" / f"{name}{suffix}"


def run_sdkmanager(root: Path, args: list[str]) -> None:
    exe = sdk_tool(root, "sdkmanager")
    env = {**os.environ, "ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}
    log(f"sdkmanager {' '.join(args)}")
    subprocess.run([str(exe), *args], check=True, env=env)


def install_packages(root: Path) -> None:
    run_sdkmanager(root, ["--licenses"])  # reads 'y' repeatedly from stdin; harmless if already accepted
    run_sdkmanager(root, ["--install", "platform-tools", "emulator", IMAGE])


def create_avd(root: Path) -> None:
    env = {**os.environ, "ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}
    exe = sdk_tool(root, "avdmanager")
    existing = subprocess.run([str(exe), "list", "avd"], capture_output=True, text=True, env=env)
    if f"Name: {AVD_NAME}" in existing.stdout:
        log(f"AVD '{AVD_NAME}' already exists")
        return
    log(f"creating AVD '{AVD_NAME}'")
    subprocess.run(
        [str(exe), "create", "avd", "-n", AVD_NAME, "-k", IMAGE, "--device", "pixel_6", "--force"],
        input="no\n",  # decline the "custom hardware profile?" prompt
        text=True,
        check=True,
        env=env,
    )


def main() -> None:
    require_java()
    root = sdk_root()
    tmp = root / ".tmp"
    root.mkdir(parents=True, exist_ok=True)
    log(f"ANDROID_HOME = {root}")

    install_cmdline_tools(root, tmp)
    install_platform_tools(root, tmp)
    install_packages(root)
    create_avd(root)

    print()
    log("done.")
    print(f"""
Add these to your environment so adb/emulator are on PATH:

  ANDROID_HOME={root}
  PATH=%PATH%;{root / 'platform-tools'};{root / 'emulator'}

Then boot the device:

  python adbctl.py boot
  python adbctl.py wait
  python adbctl.py shot frame.png
""")


if __name__ == "__main__":
    main()
