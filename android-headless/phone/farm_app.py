"""My Farm — proper control app for the redroid phone farm (zero install).

PhoneGrid-style desktop client: dark theme, one card per live phone with a
LIVE SCREEN THUMBNAIL (adb screencap, refreshed continuously in the
background), View (scrcpy window) / Connect buttons, a spawn button that
fills the live runner over SSH before ever opening a new one, runner info
and the farm log.

Stdlib + tkinter only; adb and scrcpy come as portable copies in
phone/tools/ via launch.py. Double-click Farm.cmd.
"""
from __future__ import annotations

import base64
import json
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import (BOTH, BOTTOM, LEFT, RIGHT, TOP, X, Y, Button, Canvas,
                     Frame, Label, StringVar, Text, Tk, messagebox)
from tkinter import ttk

HERE = Path(__file__).resolve().parent          # android-headless/phone
REPO = HERE.parents[1]
CAPACITY = 20
THUMB_W, THUMB_H = 180, 400                     # 720x1600 / 4
DISPATCH_URL = ("https://github.com/Zarifbenalam-2/an-ci/actions/workflows/"
                "phone-farm-redroid.yml")
UNCERTIFIED_URL = "https://www.google.com/android/uncertified"

# palette
BG, PANEL, CARD, ACCENT, TEXT, MUTED, WARN = (
    "#17181c", "#212329", "#262a33", "#22c55e", "#e8eaf0", "#8b90a0", "#f59e0b")
STATE_COLOR = {"ready": ACCENT, "booted": WARN, "booting": MUTED,
               "boot-failed": "#ef4444", "pending": MUTED}

sys.path.insert(0, str(HERE))
import launch  # noqa: E402


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def git_show(path: str) -> str:
    r = run(["git", "show", f"origin/main:{path}"], cwd=REPO)
    return r.stdout if r.returncode == 0 else ""


# --------------------------------------------------------------------- farm
class Farm:
    def __init__(self):
        self.phones: list[dict] = []
        self.ssh = ""
        self.updated = ""
        self.log_tail = ""
        self.gsf_ids = ""
        self.lock = threading.Lock()

    def refresh(self):
        run(["git", "fetch", "-q", "origin", "main"], cwd=REPO)
        raw = git_show(".session/redroid-farm.json")
        phones, ssh, updated = [], "", ""
        if raw.strip():
            try:
                data = json.loads(raw)
                phones, ssh, updated = (data.get("phones", []),
                                        data.get("ssh", ""),
                                        data.get("updated", ""))
            except json.JSONDecodeError:
                pass
        ssh = ssh or git_show(".session/farm-ssh.txt").strip()
        gsf = git_show(".session/gsf-ids.txt").strip()
        log = git_show(".session/redroid-farm-log.txt")
        with self.lock:
            self.phones, self.ssh, self.updated = phones, ssh, updated
            self.gsf_ids = gsf
            self.log_tail = "\n".join(log.strip().splitlines()[-10:])

    def ssh_target(self):
        s = self.ssh.replace("ssh://", "")
        if ":" not in s:
            return None
        host, port = s.rsplit(":", 1)
        return host, port

    def live_count(self):
        return sum(1 for p in self.phones if p.get("status") == "ready")


# ----------------------------------------------------------------- app bits
class ThumbWorker(threading.Thread):
    """Polls phone screens with adb screencap; pushes PNG bytes to a queue."""

    def __init__(self, farm, outq, adb_path):
        super().__init__(daemon=True)
        self.farm, self.outq, self.adb = farm, outq, adb_path
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.is_set():
            with self.farm.lock:
                targets = [(p["name"], p.get("adb_address", ""))
                           for p in self.farm.phones if p.get("adb_address")]
            for name, addr in targets:
                r = subprocess.run(
                    [self.adb, "-s", addr, "exec-out", "screencap", "-p"],
                    capture_output=True, timeout=20)
                if r.returncode == 0 and r.stdout[:4] == b"\x89PNG":
                    self.outq.put((name, r.stdout))
            self.stop_evt.wait(12)


class App:
    def __init__(self, root: Tk):
        self.root = root
        root.title("My Farm")
        root.configure(bg=BG)
        root.geometry("1000x680")
        self.farm = Farm()
        self.status = StringVar(value="loading …")
        self.cards: dict[str, dict] = {}       # name -> {photo, label widgets}
        self.thumbq: queue.Queue = queue.Queue()
        self.images: dict[str, object] = {}    # keep PhotoImage refs alive

        style = ttk.Style(root)
        style.theme_use("clam")
        for name in ("TFrame", "TLabel", "TButton"):
            style.configure(name, background=BG, foreground=TEXT)

        self._header()
        self._grid_area()
        self._footer()
        self.refresh()

        self.adb_path = launch.ensure_adb()
        self.worker = ThumbWorker(self.farm, self.thumbq, self.adb_path)
        self.worker.start()
        self.root.after(400, self._drain_thumbs)

    def _header(self):
        bar = Frame(self.root, bg=PANEL)
        bar.pack(side=TOP, fill=X)
        Label(bar, text="MY FARM", bg=PANEL, fg=TEXT,
              font=("Segoe UI", 16, "bold")).pack(side=LEFT, padx=14, pady=10)
        Label(bar, text="cloud phones · github runners", bg=PANEL,
              fg=MUTED).pack(side=LEFT, pady=10)
        Label(bar, textvariable=self.status, bg=PANEL, fg=MUTED).pack(
            side=RIGHT, padx=12)
        Button(bar, text="GSF register",
               command=lambda: webbrowser.open(UNCERTIFIED_URL), bg=CARD,
               fg=TEXT, activebackground="#333845", relief="flat", padx=12,
               pady=5).pack(side=RIGHT, padx=4, pady=10)
        Button(bar, text="Refresh", command=self.refresh, bg=CARD, fg=TEXT,
               activebackground="#333845", relief="flat", padx=12,
               pady=5).pack(side=RIGHT, padx=4, pady=10)
        Button(bar, text="+ Spawn phone", command=self.spawn, bg=ACCENT,
               fg="#0b0f0c", activebackground="#16a34a", relief="flat",
               padx=14, pady=5, font=("Segoe UI", 10, "bold")).pack(
               side=RIGHT, padx=6, pady=10)
        Button(bar, text="Refresh", command=self.refresh, bg=CARD, fg=TEXT,
               activebackground="#333845", relief="flat", padx=12,
               pady=5).pack(side=RIGHT, padx=4, pady=10)
        Button(bar, text="GSF register",
               command=lambda: webbrowser.open(UNCERTIFIED_URL), bg=CARD,
               fg=TEXT, activebackground="#333845", relief="flat", padx=12,
               pady=5).pack(side=RIGHT, padx=4, pady=10)
        Label(bar, textvariable=self.status, bg=PANEL, fg=MUTED).pack(
            side=RIGHT, padx=12)

    def _grid_area(self):
        wrap = Frame(self.root, bg=BG)
        wrap.pack(side=TOP, fill=BOTH, expand=True, padx=10, pady=8)
        self.canvas = Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.grid_frame = Frame(self.canvas, bg=BG)
        self.win = self.canvas.create_window(
            (0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(
                             self.win, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))

    def _footer(self):
        foot = Frame(self.root, bg=PANEL)
        foot.pack(side=BOTTOM, fill=X)
        self.footer = StringVar(value="")
        Label(foot, textvariable=self.footer, bg=PANEL, fg=MUTED,
              anchor="w", justify=LEFT).pack(side=LEFT, padx=12, pady=6)
        logbox = Text(self.root, height=5, bg="#111216", fg=MUTED,
                      insertbackground=TEXT, font=("Consolas", 8),
                      relief="flat", wrap="none")
        logbox.pack(side=BOTTOM, fill=X, padx=10, pady=(0, 8))
        self.log_box = logbox

    # -------------------------------------------------------------- refresh
    def refresh(self):
        threading.Thread(target=self._refresh_bg, daemon=True).start()

    def _refresh_bg(self):
        self.farm.refresh()
        self.root.after(0, self._redraw)

    def _redraw(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.cards.clear()
        phones = self.farm.phones
        self.status.set(
            f"{self.farm.live_count()} ready · {len(phones)} phones · "
            f"updated {self.farm.updated or '?'}")
        hp = self.farm.ssh_target()
        self.footer.set(
            f"runner ssh: {self.farm.ssh or '(none live)'}    ·    "
            f"{len(phones)}/{CAPACITY} on this runner"
            + (f"    ·    copy: ssh -p {hp[1]} {hp[0]}" if hp else ""))
        if not phones:
            Label(self.grid_frame, text="No live farm.\n\n+ Spawn phone starts "
                  "one (GitHub opens, paste a Tailscale key, Run).",
                  bg=BG, fg=MUTED, justify=LEFT, pady=40).pack()
        for i, p in enumerate(phones):
            self._card(p, row=i // 3, col=i % 3)
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", self.farm.log_tail or "(no farm log yet)")

    def _card(self, phone, row, col):
        card = Frame(self.grid_frame, bg=CARD, padx=10, pady=10)
        card.grid(row=row, column=col, sticky="nw", padx=8, pady=8)
        name = phone.get("name", "?")
        state = phone.get("status", "?")
        addr = phone.get("adb_address", "")

        head = Frame(card, bg=CARD)
        head.pack(fill=X)
        Label(head, text=name, bg=CARD, fg=TEXT,
              font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        dot = Label(head, text="●", fg=STATE_COLOR.get(state, MUTED), bg=CARD)
        dot.pack(side=RIGHT)
        Label(head, text=f" {state}", fg=STATE_COLOR.get(state, MUTED),
              bg=CARD).pack(side=RIGHT)

        thumb = Label(card, text="  waiting for screen…", bg="#111216",
                      fg=MUTED, width=26, height=21)
        thumb.pack(pady=6)
        self.cards[name] = {"thumb": thumb}

        Label(card, text=addr or "—", bg=CARD, fg=MUTED,
              font=("Consolas", 8)).pack()

        btns = Frame(card, bg=CARD)
        btns.pack(pady=4)
        Button(btns, text="View", command=lambda a=addr: self.view(a),
               bg=ACCENT, fg="#0b0f0c", relief="flat",
               padx=10).pack(side=LEFT) if addr else \
            Button(btns, text="View", state="disabled", bg=CARD, fg=MUTED,
                   relief="flat", padx=10).pack(side=LEFT)
        Button(btns, text="Connect", command=lambda a=addr: self.connect(a),
               bg="#333845", fg=TEXT, relief="flat", padx=10).pack(side=LEFT, padx=6)

    def _drain_thumbs(self):
        try:
            while True:
                name, png = self.thumbq.get_nowait()
                widget = self.cards.get(name, {}).get("thumb")
                if widget is not None:
                    from tkinter import PhotoImage
                    img = PhotoImage(data=base64.b64encode(png).decode())
                    img = img.subsample(4, 4)          # 720x1600 -> 180x400
                    self.images[name] = img            # keep a reference
                    widget.configure(image=img, text="", width=THUMB_W,
                                     height=THUMB_H)
        except queue.Empty:
            pass
        self.root.after(400, self._drain_thumbs)

    # -------------------------------------------------------------- actions
    def adb(self):
        return self.adb_path if hasattr(self, "adb_path") else launch.ensure_adb()

    def connect(self, addr):
        def work():
            r = run([self.adb(), "connect", addr])
            ok = "connected" in (r.stdout + r.stderr)
            self.root.after(0, lambda: self.status.set(
                f"adb {'OK ' + addr if ok else 'FAILED: ' + (r.stdout + r.stderr)[:70]}"))
        threading.Thread(target=work, daemon=True).start()

    def view(self, addr):
        self.connect(addr)
        subprocess.Popen([launch.ensure_scrcpy(), "-s", addr, "--no-audio",
                          "--max-fps", "15", "--video-bit-rate", "2M"])

    def spawn(self):
        def work():
            self.farm.refresh()
            self.root.after(0, self._spawn_ui)
        threading.Thread(target=work, daemon=True).start()

    def _spawn_ui(self):
        hp = self.farm.ssh_target()
        if hp and len(self.farm.phones) < CAPACITY:
            host, port = hp
            if not messagebox.askyesno(
                    "Spawn on same runner",
                    f"Runner is live with {len(self.farm.phones)}/{CAPACITY} phones.\n"
                    f"Boot phone {len(self.farm.phones) + 1} on the SAME runner via SSH?"):
                return
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
