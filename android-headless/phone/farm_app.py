"""Desktop control app for the redroid phone farm (zero install).

Double-click Farm.cmd at the repo root (or run this file) and you get a
window with one card per live phone: its status, a View button that opens
the scrcpy window, a Connect button for plain adb. One Spawn Phone button
implements the fill-the-runner policy: if the live runner still has capacity
it SSHes in and boots phone N+1 on the SAME machine; only when the runner is
full (or there is none) does it open the GitHub dispatch page for a new one.

Everything it needs (adb, scrcpy) comes from phone/tools/ via launch.py --
nothing is installed system-wide.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from tkinter import (BOTH, BOTTOM, LEFT, RIGHT, TOP, X, Button, Frame, Label,
                     Menu, Scrollbar, StringVar, Text, Tk, messagebox)

HERE = Path(__file__).resolve().parent          # android-headless/phone
REPO = HERE.parents[1]                          # repo root
SESSION = REPO / ".session"
CAPACITY = 10
DISPATCH_URL = ("https://github.com/Zarifbenalam-2/an-ci/actions/workflows/"
                "phone-farm-redroid.yml")
UNCERTIFIED_URL = "https://www.google.com/android/uncertified"

sys.path.insert(0, str(HERE))
import launch  # noqa: E402  (reuses ensure_adb / ensure_scrcpy)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def git_show(path: str) -> str:
    r = run(["git", "show", f"origin/main:{path}"], cwd=REPO)
    return r.stdout if r.returncode == 0 else ""


def git_refresh():
    run(["git", "fetch", "-q", "origin", "main"], cwd=REPO)


class Farm:
    """Everything the app knows about the live farm, from the repo beacons."""

    def __init__(self):
        self.phones: list[dict] = []
        self.ssh = ""
        self.updated = ""
        self.log_tail = ""
        self.gsf_ids = ""

    def refresh(self):
        git_refresh()
        raw = git_show(".session/redroid-farm.json")
        if raw.strip():
            try:
                data = json.loads(raw)
                self.phones = data.get("phones", [])
                self.ssh = data.get("ssh", "")
                self.updated = data.get("updated", "")
            except json.JSONDecodeError:
                pass
        self.ssh = self.ssh or git_show(".session/farm-ssh.txt").strip()
        self.gsf_ids = git_show(".session/gsf-ids.txt").strip()
        log = git_show(".session/redroid-farm-log.txt")
        self.log_tail = "\n".join(log.strip().splitlines()[-8:])

    def ssh_host_port(self):
        # "ssh://runner@100.x.y.z:2222" -> ("runner@100.x.y.z", "2222")
        s = self.ssh.replace("ssh://", "")
        if ":" not in s:
            return None
        host, port = s.rsplit(":", 1)
        return host, port

    def live_count(self):
        return sum(1 for p in self.phones if p.get("status") == "ready")


class App:
    def __init__(self, root: Tk):
        self.root = root
        root.title("My Farm — redroid control")
        root.geometry("860x560")
        self.farm = Farm()
        self.status = StringVar(value="loading …")

        top = Frame(root)
        top.pack(side=TOP, fill=X, padx=8, pady=6)
        Button(top, text="Refresh", command=self.refresh).pack(side=LEFT)
        Button(top, text="+ Spawn phone", command=self.spawn).pack(side=LEFT, padx=8)
        Button(top, text="Register GSF IDs", command=lambda: webbrowser.open(UNCERTIFIED_URL)).pack(side=LEFT)
        Label(top, textvariable=self.status).pack(side=RIGHT)

        self.grid_frame = Frame(root)
        self.grid_frame.pack(side=TOP, fill=BOTH, expand=True, padx=8)

        bottom = Frame(root)
        bottom.pack(side=BOTTOM, fill=X, padx=8, pady=6)
        self.footer = StringVar(value="")
        Label(bottom, textvariable=self.footer, justify=LEFT, anchor="w").pack(side=LEFT)

        log = Frame(root)
        log.pack(side=BOTTOM, fill=X, padx=8)
        self.log_box = Text(log, height=5, wrap="none", font=("Consolas", 8))
        self.log_box.pack(side=LEFT, fill=BOTH, expand=True)
        sb = Scrollbar(log, command=self.log_box.yview)
        sb.pack(side=RIGHT, fill="y")
        self.log_box.config(yscrollcommand=sb.set)

        self.refresh()

    # ---------------------------------------------------------------- actions
    def refresh(self):
        self.farm.refresh()
        for w in self.grid_frame.winfo_children():
            w.destroy()
        phones = self.farm.phones
        self.status.set(
            f"{self.farm.live_count()} ready · {len(phones)} phones · "
            f"updated {self.farm.updated or '?'}")
        self.footer.set(
            f"runner ssh: {self.farm.ssh or '(none live)'}   ·   "
            f"capacity {len(phones)}/{CAPACITY} on this runner")
        if not phones:
            Label(self.grid_frame, text="No live farm.\nSpawn one via the button "
                  "above (opens GitHub, paste a Tailscale key, Run).",
                  justify="left", pady=20).pack()
        else:
            for i, p in enumerate(phones):
                self._card(p, row=i // 3, col=i % 3)
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", self.farm.log_tail or "(no farm log yet)")

    def _card(self, phone: dict, row: int, col: int):
        card = Frame(self.grid_frame, bd=1, relief="solid", padx=10, pady=8)
        card.grid(row=row, column=col, sticky="nwe", padx=6, pady=6)
        name = phone.get("name", "?")
        state = phone.get("status", "?")
        addr = phone.get("adb_address", "")
        color = {"ready": "#0a7f24", "booted": "#b8860b", "booting": "#888",
                 "boot-failed": "#c00"}.get(state, "#888")
        Label(card, text=name, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        Label(card, text=state, fg=color).pack(anchor="w")
        Label(card, text=addr or "—", fg="#555").pack(anchor="w")
        row_btn = Frame(card)
        row_btn.pack(anchor="w", pady=4)
        Button(row_btn, text="View", command=lambda a=addr: self.view(a),
               state="normal" if addr else "disabled").pack(side=LEFT)
        Button(row_btn, text="Connect", command=lambda a=addr: self.connect(a),
               state="normal" if addr else "disabled").pack(side=LEFT, padx=4)

    def adb(self) -> str:
        return launch.ensure_adb()

    def scrcpy(self) -> str:
        return launch.ensure_scrcpy()

    def connect(self, addr: str):
        r = run([self.adb(), "connect", addr])
        ok = "connected" in (r.stdout + r.stderr)
        self.status.set(f"adb {'OK: ' + addr if ok else 'FAILED: ' + (r.stdout + r.stderr)[:80]}")

    def view(self, addr: str):
        self.connect(addr)
        subprocess.Popen([self.scrcpy(), "-s", addr,
                          "--max-fps", "15", "--video-bit-rate", "2M"])

    def spawn(self):
        """Fill the live runner first; only dispatch a new one when full."""
        self.farm.refresh()
        hp = self.farm.ssh_host_port()
        if hp and len(self.farm.phones) < CAPACITY:
            host, port = hp
            if messagebox.askyesno(
                    "Spawn on same runner",
                    f"Runner is live with {len(self.farm.phones)}/{CAPACITY} phones.\n"
                    f"Boot phone {len(self.farm.phones) + 1} on the SAME runner via SSH?"):
                r = subprocess.run(
                    ["ssh", "-p", port, "-o", "StrictHostKeyChecking=accept-new",
                     "-o", "BatchMode=yes", host,
                     "bash android-headless/redroid/add-phone.sh"],
                    capture_output=True, text=True, timeout=300)
                out = (r.stdout + r.stderr).strip()
                if r.returncode == 3:
                    self._open_dispatch("Runner is at capacity — open a NEW runner?")
                elif r.returncode == 0:
                    messagebox.showinfo("Spawned", out or "phone added")
                    self.refresh()
                else:
                    messagebox.showerror("Spawn failed", out[:500] or f"exit {r.returncode}")
            return
        self._open_dispatch()

    def _open_dispatch(self, msg="No live runner — open GitHub to dispatch a new one?"):
        if messagebox.askyesno("New runner", msg):
            webbrowser.open(DISPATCH_URL)


def main() -> int:
    root = Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
