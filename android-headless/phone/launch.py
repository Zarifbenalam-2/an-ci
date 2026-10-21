#!/usr/bin/env python3
"""One-button launcher: bring up an Android device and open a live window onto it.

  python phone/launch.py local                        # emulator on this PC
  python phone/launch.py remote --vm ubuntu@1.2.3.4   # emulator on a cloud VM

Either way you end up with a window you control with the mouse and keyboard,
exactly like BlueStacks, plus a real adb connection that scripts (and this
agent) can drive at the same time.

Everything it needs -- adb and scrcpy -- is downloaded into phone/tools/ and run
from there, so nothing is installed system-wide and nothing needs admin rights.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "tools"
ADB_PORT = 5555


def log(msg: str) -> None:
    print(f"[phone] {msg}", flush=True)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    log(f"  -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def latest_asset(repo: str, pattern: str) -> tuple[str, str]:
    """Return (download_url, filename) for the newest release asset matching pattern."""
    with urllib.request.urlopen(f"https://api.github.com/repos/{repo}/releases/latest") as resp:
        data = json.load(resp)
    for asset in data.get("assets", []):
        if re.search(pattern, asset["name"]):
            return asset["browser_download_url"], asset["name"]
    raise RuntimeError(f"no asset matching {pattern!r} in {repo}'s latest release")


def ensure_adb() -> str:
    """Find adb, or fetch Android platform-tools as a portable copy."""
    found = shutil.which("adb") or shutil.which("adb.exe")
    if found:
        return found
    existing = sorted(TOOLS.glob("platform-tools*/adb.exe")) + sorted(TOOLS.glob("platform-tools*/adb"))
    if existing:
        return str(existing[0])

    url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    if os.name != "nt":
        url = "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
    zip_path = download(url, TOOLS / "platform-tools.zip")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(TOOLS)
    exe = TOOLS / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    if os.name != "nt":
        exe.chmod(0o755)
    log(f"adb ready: {exe}")
    return str(exe)


def ensure_scrcpy() -> str:
    """Find scrcpy, or fetch the portable release zip (it is not a system install)."""
    found = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
    if found:
        return found
    pattern = "scrcpy-win64-v.*\\.zip" if os.name == "nt" else "scrcpy-linux-v.*\\.tar\\.gz"
    for candidate in sorted(TOOLS.glob("scrcpy-*/scrcpy.exe"), reverse=True) + \
                     sorted(TOOLS.glob("scrcpy-*/scrcpy")):
        return str(candidate)

    url, name = latest_asset("Genymobile/scrcpy", pattern)
    archive = download(url, TOOLS / name)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(TOOLS)
        exe = sorted(TOOLS.glob("scrcpy-*/scrcpy.exe"), reverse=True)[0]
    else:
        import tarfile
        with tarfile.open(archive) as tf:
            tf.extractall(TOOLS)
        exe = sorted(TOOLS.glob("scrcpy-*/scrcpy"), reverse=True)[0]
        exe.chmod(0o755)
    log(f"scrcpy ready: {exe}")
    return str(exe)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_port(port: int, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open("127.0.0.1", port):
            return
        time.sleep(0.5)
    raise TimeoutError(f"nothing listening on localhost:{port} after {timeout}s")


# --- remote host ------------------------------------------------------------

REMOTE_BOOT = (
    'export ANDROID_HOME="$HOME/android-sdk"; '
    'export PATH="$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$PATH"; '
    'if ! adb devices | grep -q "emulator-"; then '
    '  nohup emulator -avd headless -no-window -no-audio -no-boot-anim -no-snapshot '
    '    -gpu swiftshader_indirect >"$HOME/emulator.log" 2>&1 & '
    '  echo "started emulator"; '
    "else echo 'emulator already running'; fi; "
    "adb wait-for-device >/dev/null 2>&1; "
    'i=0; until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d \'\\r\')" = "1" ]; do '
    '  i=$((i+1)); [ $i -gt 100 ] && { echo "boot timeout"; exit 1; }; sleep 3; done; '
    'echo booted'
)


def ensure_remote_device(vm: str, skip_start: bool) -> None:
    if skip_start:
        log(f"leaving {vm}'s device alone (--skip-start)")
        return
    log(f"asking {vm} to bring the emulator up (starts it only if it is not already running)")
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", vm, REMOTE_BOOT], text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"could not bring the emulator up on {vm}. Is `ssh {vm}` working without a "
            f"password? Run provision.sh there first."
        )


def open_tunnel(vm: str) -> subprocess.Popen:
    """Forward the VM's emulator adb port to this machine so adb sees it as local."""
    log(f"opening ssh tunnel: localhost:{ADB_PORT} -> {vm}:{ADB_PORT}")
    proc = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         "-L", f"{ADB_PORT}:localhost:{ADB_PORT}", vm],
    )
    wait_port(ADB_PORT)
    return proc


# --- local host -------------------------------------------------------------

def ensure_jdk() -> str | None:
    """The SDK's sdkmanager/avdmanager are Java programs. Return a PATH dir with
    java in it, or None if java is already available."""
    if shutil.which("java"):
        return None
    found = sorted(TOOLS.glob("jdk-*/bin/java.exe")) + sorted(TOOLS.glob("jdk-*/bin/java"))
    if found:
        return str(found[0].parent)
    url = ("https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk"
           if os.name == "nt" else
           "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk")
    log("fetching a portable JDK 17 (the SDK's tools are Java programs)")
    if "windows" in url:
        name = "jdk-portable.zip"
        archive = download(url, TOOLS / "jdk-portable.zip")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(TOOLS)
        exe = sorted(TOOLS.glob("jdk-*/bin/java.exe"))[0]
    else:
        import tarfile
        archive = download(url, TOOLS / "jdk-portable.tar.gz")
        import tarfile
        with tarfile.open(archive) as tf:
            tf.extractall(TOOLS)
        exe = sorted(TOOLS.glob("jdk-*/bin/java"))[0]
        exe.chmod(0o755)
    log(f"portable JDK ready: {exe.parent}")
    return str(exe.parent)


def ensure_local_toolchain() -> None:
    """Make sure a local Android SDK + emulator exist, installing if not."""
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or (Path.home() / "Android" / "Sdk"))
    emulator = sdk / "emulator" / ("emulator.exe" if os.name == "nt" else "emulator")
    if emulator.exists():
        return

    log("no local Android SDK found -- installing one into phone/tools/ (portable, no admin)")
    log("this is a one-time ~10 GB download and takes 10-20 minutes")
    env = dict(os.environ)
    if java_bin := ensure_jdk():
        env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
    script = HERE.parent / "setup_sdk.py"
    env["ANDROID_HOME"] = str(TOOLS / "android-sdk")
    result = subprocess.run([sys.executable, str(HERE.parent / "setup_sdk.py")], env=env)
    if result.returncode != 0:
        sys.exit(
            "could not install the local SDK. The usual cause is a missing JDK 17 --\n"
            "  install one (e.g. winget install --id EclipseAdoptium.Temurin.17.JDK)\n"
            "  then run this again."
        )
    os.environ["ANDROID_HOME"] = env["ANDROID_HOME"]


def ensure_local_device(adb: str) -> None:
    """Boot the local AVD if nothing is attached yet."""
    out = run([adb, "devices"]).stdout
    if "emulator-" in out:
        log("a local emulator is already attached")
        return

    ensure_local_toolchain()

    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or (Path.home() / "Android" / "Sdk"))
    emulator = sdk / "emulator" / ("emulator.exe" if os.name == "nt" else "emulator")
    if not emulator.exists():
        sys.exit(f"no emulator at {emulator} even after provisioning")

    log("booting the local emulator (no window; scrcpy provides the view)")
    subprocess.Popen(
        [str(emulator), "-avd", os.environ.get("AVD_NAME", "headless"),
         "-no-window", "-no-audio", "-no-boot-anim", "-no-snapshot",
         "-gpu", "swiftshader_indirect"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log("waiting for boot")
    run([adb, "wait-for-device"])
    deadline = time.time() + 420
    while time.time() < deadline:
        if run([adb, "shell", "getprop", "sys.boot_completed"]).stdout.strip() == "1":
            break
        time.sleep(3)
    else:
        sys.exit("the local emulator did not finish booting; see the emulator window output")


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["local", "remote"])
    ap.add_argument("--vm", default=os.environ.get("PHONE_VM"),
                    help="ssh target of the VM, e.g. ubuntu@1.2.3.4 (or set PHONE_VM)")
    ap.add_argument("--serial", help="attach to an already-connected device instead")
    ap.add_argument("--no-view", action="store_true", help="connect but do not open the window")
    ap.add_argument("--max-size", type=int, default=1024, help="window size cap in pixels")
    ap.add_argument("--skip-start", action="store_true",
                    help="do not try to start the emulator on the VM")
    args = ap.parse_args()

    sys.path.insert(0, str(HERE.parent))
    from adbctl import AndroidDevice  # noqa: E402  (import after path setup)

    adb = ensure_adb()
    scrcpy = None if args.no_view else ensure_scrcpy()

    tunnel = None
    serial = args.serial

    try:
        if args.mode == "remote":
            if not args.vm:
                sys.exit("remote mode needs --vm user@host (or set PHONE_VM)")
            ensure_remote_device(args.vm, args.skip_start)
            tunnel = open_tunnel(args.vm)
            run([adb, "connect", f"localhost:{ADB_PORT}"])
            serial = serial or f"localhost:{ADB_PORT}"
        else:
            ensure_local_device(adb)

        dev = AndroidDevice(serial=serial, adb=adb)
        if args.mode == "local" and serial is None:
            dev.wait_for_boot(timeout=420)
        connected = dev.devices()
        log(f"devices attached: {connected or '(none)'}")
        if not connected:
            sys.exit("no device attached; nothing to show")

        if serial is None:
            serial = connected[0]

        print()
        log(f"device ready: {serial}")
        print(f"""
  Live window      -- a scrcpy window opens next; click and type in it directly.
  Install an APK   python android-headless/adbctl.py --serial {serial} install-apk app.apk
  Send a file in   python android-headless/adbctl.py --serial {serial} push photo.jpg
  Pull a file out  python android-headless/adbctl.py --serial {serial} pull /sdcard/Download/x.png .
  Scripted input   python android-headless/adbctl.py --serial {serial} tap 540 1200
""")

        if scrcpy:
            log("opening the live window (close it to end the session)")
            code = AndroidDevice(serial=serial, adb=adb).view(max_size=args.max_size)
            return code
        return 0
    finally:
        if tunnel:
            log("closing the ssh tunnel")
            tunnel.terminate()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
