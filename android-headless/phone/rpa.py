#!/usr/bin/env python3
"""rpa.py -- the workflow engine: PhoneGrid's RPA node set, as JSON templates.

A template is a JSON file:

    {"name": "tour", "params": {"n": 3}, "steps": [ ...nodes... ]}

Nodes (the vendor's names map 1:1):
  wake                                  screen on
  launch_app  package= com.x.y  [activity=.Main]     (activity optional)
  stop_app    package= com.x.y
  tap         x= y=
  tap_element text="Sign in" | contains= | resource_id=  (view-tree lookup)
  swipe       x1 y1 x2 y2  duration=[250,450]        (random within range)
  input_text  text=                                      (spaces escaped)
  key         name= BACK|HOME|WAKEUP...
  wait        seconds=[1,3] or 2                       (random within range)
  loop        times= n (or [min,max])  steps=[ ...nodes... ]
  screenshot  name= step1                              (png into out dir)
  adb         command= "dumpsys window | grep Focus"   (raw shell)

Randomized ranges are the human-feel knobs: a swipe/wait given [min,max]
picks a different value every run.

Usage:
  python rpa.py --template farm/templates/tour.json \
                --phones 100.85.165.11:21001,100.85.165.11:21002 \
                --adb <adb-path> --out runs/rpa [--params '{"n":5}']
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import launch  # noqa: E402  (portable adb)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Phone:
    def __init__(self, adb: str, serial: str, out: Path):
        self.adb, self.serial, self.out = adb, serial, out
        out.mkdir(parents=True, exist_ok=True)

    def shell(self, cmd: str, timeout: int = 60) -> str:
        r = subprocess.run([self.adb, "-s", self.serial, "shell", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()

    def screenshot(self, name: str) -> str:
        png = subprocess.run([self.adb, "-s", self.serial, "exec-out",
                              "screencap", "-p"], capture_output=True, timeout=60)
        path = self.out / f"{name}.png"
        path.write_bytes(png.stdout)
        return str(path)

    # --- view tree (uiautomator) for element lookup ---------------------
    def find_element(self, text=None, contains=None, resource_id=None,
                     desc=None) -> tuple[int, int] | None:
        xml = self.shell("uiautomator dump /dev/tty 2>/dev/null")
        m = re.search(r"<hierarchy.*</hierarchy>", xml, re.S)
        if not m:
            return None
        try:
            root = ET.fromstring(m.group(0))
        except ET.ParseError:
            return None
        for node in root.iter("node"):
            label = (node.get("text", "") or "").strip()
            rid = node.get("resource-id", "") or ""
            d = node.get("content-desc", "") or ""
            hit = ((text is not None and label == text)
                   or (contains is not None and contains in (label + " " + d))
                   or (resource_id is not None and rid.endswith(resource_id))
                   or (desc is not None and d == desc))
            if hit:
                b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
                if b:
                    x1, y1, x2, y2 = map(int, b.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
        return None


def rnd(v):
    """[min,max] -> random pick; number -> itself."""
    if isinstance(v, list) and len(v) == 2:
        return random.uniform(v[0], v[1])
    return v


class Engine:
    def __init__(self, phone: Phone, params: dict):
        self.p, self.params = phone, params

    def run_steps(self, steps: list[dict], prefix: str = "") -> None:
        for i, node in enumerate(steps, 1):
            act = node.get("action")
            tag = f"{prefix}step {i} {act}"
            try:
                self.run_node(act, node, tag, prefix)
            except Exception as exc:  # noqa: BLE001
                log(f"{tag} ERROR: {exc}")
                raise

    def run_node(self, act, node, tag, prefix=''):
        sh = self.p.shell
        if act == "wake":
            sh("input keyevent KEYCODE_WAKEUP"); log(f"{tag} ok")
        elif act == "launch_app":
            pkg = self.subst(node["package"])
            if node.get("activity"):
                sh(f"am start -n {pkg}/{self.subst(node['activity'])}")
            else:
                sh(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            log(f"{tag} -> {pkg}")
        elif act == "stop_app":
            sh(f"am force-stop {self.subst(node['package'])}"); log(f"{tag} ok")
        elif act == "tap":
            x, y = int(node["x"]), int(node["y"])
            sh(f"input tap {x} {y}"); log(f"{tag} ({x},{y})")
        elif act == "tap_element":
            xy = self.p.find_element(
                text=self.subst(node.get("text")),
                contains=self.subst(node.get("contains")),
                resource_id=node.get("resource_id"),
                desc=self.subst(node.get("desc")))
            if not xy:
                raise RuntimeError("element not found on screen")
            sh(f"input tap {xy[0]} {xy[1]}"); log(f"{tag} at {xy}")
        elif act == "swipe":
            d = int(rnd(node.get("duration", [250, 450])))
            sh("input swipe {x1} {y1} {x2} {y2} {d}".format(
                x1=node["x1"], y1=node["y1"], x2=node["x2"], y2=node["y2"], d=d))
            log(f"{tag} dur={d}ms")
        elif act == "input_text":
            sh("input text " + self.subst(node["text"]).replace(" ", "%s"))
            log(f"{tag} ok")
        elif act == "key":
            sh(f"input keyevent KEYCODE_{node['name']}"); log(f"{tag} {node['name']}")
        elif act == "wait":
            s = rnd(node.get("seconds", 2))
            time.sleep(min(s, 60)); log(f"{tag} {s:.1f}s")
        elif act == "loop":
            n = rnd(self.subst(node.get("times", 1)))
            n = int(float(n)) if not isinstance(n, list) else int(rnd(n))
            log(f"{tag} x{n}")
            for k in range(n):
                self.run_steps(node["steps"], prefix=f"{prefix}loop{k + 1}/{n}.")
        elif act == "screenshot":
            path = self.p.screenshot(self.subst(node.get("name", f"shot{int(time.time())}")))
            log(f"{tag} -> {path}")
        elif act == "adb":
            out = sh(self.subst(node["command"]))
            log(f"{tag}: {out[:160]}")
        else:
            raise RuntimeError(f"unknown action {act!r}")

    def subst(self, v):
        if isinstance(v, str):
            for k, val in self.params.items():
                v = v.replace("{{%s}}" % k, str(val))
        return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True)
    ap.add_argument("--phones", required=True, help="comma-separated adb serials")
    ap.add_argument("--adb", default=None)
    ap.add_argument("--out", default="runs/rpa")
    ap.add_argument("--params", default="{}", help='JSON, e.g. \'{"n":3}\'')
    a = ap.parse_args()

    tpl = json.loads(Path(a.template).read_text(encoding="utf-8"))
    params = dict(tpl.get("params", {})); params.update(json.loads(a.params))
    adb = a.adb or launch.ensure_adb()
    outroot = Path(a.out)

    rc = 0
    for serial in a.phones.split(","):
        serial = serial.strip()
        if not serial:
            continue
        log(f"===== {serial}: {tpl.get('name', 'template')} =====")
        ph = Phone(adb, serial, outroot / serial.replace(":", "_"))
        try:
            Engine(ph, params).run_steps(tpl["steps"])
            log(f"===== {serial}: DONE =====")
        except Exception as exc:  # noqa: BLE001
            log(f"===== {serial}: FAILED: {exc} =====")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
