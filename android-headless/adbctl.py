#!/usr/bin/env python3
"""Control a headless Android device from Python via adb.

Usable as a library (`from adbctl import AndroidDevice`) or a CLI:

  python adbctl.py boot                 start the AVD with no window
  python adbctl.py wait                 block until the device finishes booting
  python adbctl.py shot out.png         screenshot to a file
  python adbctl.py tap 540 1200         tap at pixel coordinates
  python adbctl.py swipe 540 1600 540 600
  python adbctl.py text "hello"         type into the focused field
  python adbctl.py key 4                send a keyevent (4 = BACK)
  python adbctl.py open com.foo.bar     launch an app package
  python adbctl.py shell "getprop"      run an arbitrary adb shell command
  python adbctl.py stop                 kill the emulator
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

AVD_NAME = os.environ.get("AVD_NAME", "headless")
IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

# Key events worth naming rather than memorising.
KEY = {
    "home": 3, "back": 4, "call": 5, "endcall": 6, "volup": 24, "voldown": 25,
    "power": 26, "camera": 27, "clear": 28, "enter": 66, "del": 67, "tab": 61,
    "space": 62, "menu": 82, "search": 84, "playpause": 85, "stop": 86,
    "next": 87, "prev": 88, "mute": 91, "pageup": 92, "pagedown": 93,
    "wakeup": 224, "sleep": 223, "appswitch": 187,
}


def sdk_root() -> Path:
    env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if env:
        return Path(env).expanduser()
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Android" / "Sdk"
    return Path.home() / "Android" / "Sdk"


def _tool(name: str) -> str:
    """Resolve a tool from ANDROID_HOME, falling back to PATH."""
    candidates = [
        sdk_root() / "platform-tools" / f"{name}{EXE}",
        sdk_root() / "emulator" / f"{name}{EXE}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} not found. Run `python setup_sdk.py` or add "
        f"{sdk_root() / 'platform-tools'} to PATH."
    )


class AndroidDevice:
    """A thin, explicit wrapper over adb. Every method is one adb invocation."""

    def __init__(self, serial: str | None = None, adb: str | None = None) -> None:
        self.adb = adb or _tool("adb")
        self.serial = serial

    # -- plumbing ---------------------------------------------------------

    def _base(self) -> list[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, args: list[str], *, binary: bool = False, check: bool = True,
            timeout: int | None = None):
        """Run an adb command. Returns str, or bytes when binary=True."""
        result = subprocess.run(
            self._base() + args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"adb {' '.join(args)} failed: {err or result.returncode}")
        return result.stdout if binary else result.stdout.decode(errors="replace")

    def shell(self, command: str, *, check: bool = True) -> str:
        return self.run(["shell", command], check=check).strip()

    # -- lifecycle --------------------------------------------------------

    def devices(self) -> list[str]:
        out = self.run(["devices"])
        serials = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def boot(self, avd: str = AVD_NAME, *, window: bool = False,
             gpu: str = "swiftshader_indirect", extra: list[str] | None = None) -> subprocess.Popen:
        """Start the emulator detached. Headless by default."""
        cmd = [_tool("emulator"), "-avd", avd, "-no-snapshot-save", "-no-boot-anim"]
        cmd += ["-gpu", gpu]
        if not window:
            cmd += ["-no-window", "-no-audio"]
        cmd += extra or []
        print(f"[adbctl] starting: {' '.join(cmd)}", flush=True)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
        )

    def wait_for_device(self, timeout: int = 180) -> str:
        serial = self.run(["wait-for-device"], timeout=timeout).strip()
        if not self.serial:
            found = self.devices()
            if found:
                self.serial = found[0]
        return serial

    def boot_completed(self) -> bool:
        return self.shell("getprop sys.boot_completed", check=False).strip() == "1"

    def wait_for_boot(self, timeout: int = 300, poll: float = 2.0) -> None:
        """Block until the framework is actually up, not merely until adb connects."""
        self.wait_for_device(timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.boot_completed():
                # Animations off makes scripted input land predictably.
                self.disable_animations()
                return
            time.sleep(poll)
        raise TimeoutError(f"device did not finish booting within {timeout}s")

    def disable_animations(self) -> None:
        for scale in ("window_animation_scale", "transition_animation_scale",
                      "animator_duration_scale"):
            self.shell(f"settings put global {scale} 0.0", check=False)

    def stop(self) -> None:
        self.run(["emu", "kill"], check=False)

    # -- input ------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def text(self, value: str) -> None:
        """Type into the focused field. `input text` cannot send spaces literally."""
        self.shell("input text " + value.replace(" ", "%s"))

    def key(self, code: int | str) -> None:
        if isinstance(code, str):
            code = KEY.get(code.lower(), code)
        self.shell(f"input keyevent {code}")

    # -- screen -----------------------------------------------------------

    def screenshot_bytes(self) -> bytes:
        """Raw PNG. Uses exec-out so the bytes never touch the device filesystem."""
        return self.run(["exec-out", "screencap", "-p"], binary=True)

    def screenshot(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.screenshot_bytes())
        return dest

    def screen_size(self) -> tuple[int, int]:
        """(width, height) in pixels, from `wm size`."""
        out = self.shell("wm size")
        for line in out.splitlines():
            if ":" in line:
                dims = line.split(":")[-1].strip()
                w, h = dims.split("x")
                return int(w), int(h)
        raise RuntimeError(f"could not parse screen size from: {out!r}")

    # -- apps -------------------------------------------------------------

    def list_packages(self, third_party_only: bool = True) -> list[str]:
        flag = "-3" if third_party_only else ""
        out = self.shell(f"pm list packages {flag}".strip())
        return sorted(line.removeprefix("package:").strip() for line in out.splitlines() if line)

    def start_app(self, package: str, activity: str | None = None) -> str:
        if activity:
            component = f"{package}/{activity}"
            return self.shell(f"am start -n {component}")
        return self.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        )

    def open_url(self, url: str) -> str:
        return self.shell(f"am start -a android.intent.action.VIEW -d '{url}'")

    def install(self, apk: str | Path, replace: bool = True) -> str:
        args = ["install"]
        if replace:
            args.append("-r")
        args.append(str(apk))
        return self.run(args)

    def uninstall(self, package: str) -> str:
        return self.run(["uninstall", package])

    # -- port forwarding --------------------------------------------------

    def forward(self, local: int, remote: int) -> str:
        return self.run(["forward", f"tcp:{local}", f"tcp:{remote}"])

    # -- file transfer ----------------------------------------------------

    def push(self, local: str | Path, remote: str = "/sdcard/Download/") -> str:
        """Copy a file from this machine onto the device."""
        src = Path(local)
        if not src.exists():
            raise FileNotFoundError(src)
        # A directory target only works if it already exists on the device.
        dest = remote
        if dest.endswith("/"):
            dest += src.name
        return self.run(["push", str(src), dest])

    def pull(self, remote: str, local: str | Path) -> str:
        """Copy a file off the device onto this machine."""
        dest = Path(local)
        if dest.is_dir():
            dest = dest / Path(remote).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        return self.run(["pull", remote, str(dest)])

    def install_apk(self, apk: str | Path) -> str:
        """Install an APK from this machine, replacing any existing build."""
        return self.install(apk, replace=True)

    # -- live view --------------------------------------------------------

    def scrcpy_path(self) -> str:
        """Locate scrcpy: PATH first, then the copy phone/launch.py downloads."""
        found = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
        if found:
            return found
        tools = Path(__file__).resolve().parent / "phone" / "tools"
        for candidate in sorted(tools.glob("scrcpy-win*/scrcpy.exe"), reverse=True):
            return str(candidate)
        raise FileNotFoundError(
            "scrcpy not found. Run `python phone/launch.py` once to fetch a portable copy."
        )

    def view(self, max_size: int = 1024, extra: list[str] | None = None) -> int:
        """Open a live, mouse-controllable window onto the device, like BlueStacks.

        scrcpy mirrors the screen over adb and forwards mouse/keyboard back, so
        the same connection that scripts use is also the one you click on.
        """
        # --no-audio: redroid images ship no opus encoder and scrcpy 5.x
        # dies on the audio stream without it (Server connection failed)
        cmd = [self.scrcpy_path(), "--adb", self.adb, "--no-audio",
               "--max-size", str(max_size)]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += extra or []
        return subprocess.run(cmd, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", help="target device serial (default: only device)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("boot").add_argument("--window", action="store_true",
                                        help="show the emulator window")
    sub.add_parser("stop")
    sub.add_parser("wait").add_argument("--timeout", type=int, default=300)
    sub.add_parser("devices")
    sub.add_parser("size")
    sub.add_parser("packages")

    p = sub.add_parser("shot")
    p.add_argument("path", nargs="?", default="screen.png")

    p = sub.add_parser("tap")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)

    p = sub.add_parser("swipe")
    p.add_argument("x1", type=int)
    p.add_argument("y1", type=int)
    p.add_argument("x2", type=int)
    p.add_argument("y2", type=int)
    p.add_argument("--ms", type=int, default=300)

    p = sub.add_parser("text")
    p.add_argument("value")

    p = sub.add_parser("key")
    p.add_argument("code", help="keyevent number or name (back, home, enter, ...)")

    p = sub.add_parser("open")
    p.add_argument("target", help="package name, or a URL")

    p = sub.add_parser("shell")
    p.add_argument("command")

    p = sub.add_parser("push", help="copy a file from here onto the device")
    p.add_argument("local")
    p.add_argument("remote", nargs="?", default="/sdcard/Download/")

    p = sub.add_parser("pull", help="copy a file off the device onto here")
    p.add_argument("remote")
    p.add_argument("local")

    p = sub.add_parser("install-apk")
    p.add_argument("apk")

    p = sub.add_parser("view", help="live mouse-controllable window (scrcpy)")
    p.add_argument("--max-size", type=int, default=1024)

    p = sub.add_parser("show")
    p.add_argument("--max-size", type=int, default=1024)

    args = parser.parse_args()
    dev = AndroidDevice(serial=args.serial)

    if args.cmd == "boot":
        dev.boot(window=args.window)
    elif args.cmd == "stop":
        dev.stop()
    elif args.cmd == "wait":
        dev.wait_for_boot(timeout=args.timeout)
        print("booted")
    elif args.cmd == "devices":
        for serial in dev.devices():
            print(serial)
    elif args.cmd == "size":
        w, h = dev.screen_size()
        print(f"{w}x{h}")
    elif args.cmd == "packages":
        print("\n".join(dev.list_packages()))
    elif args.cmd == "shot":
        print(dev.screenshot(args.path))
    elif args.cmd == "tap":
        dev.tap(args.x, args.y)
    elif args.cmd == "swipe":
        dev.swipe(args.x1, args.y1, args.x2, args.y2, args.ms)
    elif args.cmd == "text":
        dev.text(args.value)
    elif args.cmd == "key":
        dev.key(args.code)
    elif args.cmd == "open":
        if "://" in args.target or args.target.startswith("www."):
            print(dev.open_url(args.target))
        else:
            print(dev.start_app(args.target))
    elif args.cmd == "shell":
        print(dev.shell(args.command))
    elif args.cmd == "push":
        print(dev.push(args.local, args.remote))
    elif args.cmd == "pull":
        print(dev.pull(args.remote, args.local))
    elif args.cmd == "install-apk":
        print(dev.install_apk(args.apk))
    elif args.cmd in ("view", "show"):
        # `show` is the older spelling; both do the same thing.
        sys.exit(dev.view(max_size=args.max_size))


if __name__ == "__main__":
    main()
