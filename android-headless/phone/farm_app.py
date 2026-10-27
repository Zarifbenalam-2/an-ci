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
import calendar
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import (BOTH, BOTTOM, LEFT, RIGHT, TOP, X, Y, BooleanVar,
                     Button, Canvas, Checkbutton, Entry, Frame, Label,
                     Listbox, StringVar, Text, Tk, Toplevel, messagebox)
from tkinter import ttk

HERE = Path(__file__).resolve().parent          # android-headless/phone
REPO = HERE.parents[1]
CAPACITY = 20
THUMB_W, THUMB_H = 180, 400                     # 720x1600 / 4
DISPATCH_URL = ("https://github.com/Zarifbenalam-2/an-ci/actions/workflows/"
                "phone-farm-redroid.yml")
UNCERTIFIED_URL = "https://www.google.com/android/uncertified"
PROFILES_API = ("https://api.github.com/repos/Zarifbenalam-2/farm-profiles/"
                "releases/tags/profiles")
ASSETS_API = ("https://api.github.com/repos/Zarifbenalam-2/farm-profiles/"
              "releases/assets")
RUNS_API = "https://api.github.com/repos/Zarifbenalam-2/an-ci/actions/runs"
CANCEL_API = ("https://api.github.com/repos/Zarifbenalam-2/an-ci/actions/"
              "runs/{rid}/cancel")
FARM_DISPATCH = ("https://api.github.com/repos/Zarifbenalam-2/an-ci/actions/"
                 "workflows/phone-farm-redroid.yml/dispatches")
FARM_MAX_AGE_S = 6 * 3600      # a farm job can live at most ~6 h on GitHub

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


def gh_token() -> str:
    """Token for github.com from the local credential store (memory only)."""
    r = run(["git", "credential", "fill"], cwd=REPO,
            input="protocol=https\nhost=github.com\n\n")
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return ""


def gh_api(url: str, token: str, method: str = "GET") -> dict:
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "farm-app"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except Exception:
        return {}


def fetch_saved_profiles():
    """Profile assets in the private farm-profiles release; None = lookup failed."""
    tok = gh_token()
    if not tok:
        return None
    rel = gh_api(PROFILES_API, tok)
    if "assets" not in rel:
        return None
    assets = [a for a in rel["assets"]
              if a.get("name", "").startswith("rfarm")
              and a.get("name", "").endswith(".tar.gz")]
    assets.sort(key=lambda a: a.get("name", ""))
    return assets


def delete_profile_asset(aid, token: str) -> bool:
    req = urllib.request.Request(f"{ASSETS_API}/{aid}", method="DELETE",
                                 headers={"Authorization": f"Bearer {token}",
                                          "User-Agent": "farm-app"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def gh_post(token: str, url: str, payload: dict = None) -> bool:
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(url, method="POST", data=data, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "farm-app"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


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
        # ghost guard: a farm job lives at most ~6 h; older status files are
        # leftovers of a dead runner and must not render as phone cards
        if updated:
            try:
                t = calendar.timegm(time.strptime(updated, "%Y-%m-%d %H:%M:%S"))
                if time.time() - t > FARM_MAX_AGE_S:
                    phones, ssh = [], ""
            except ValueError:
                pass
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
        self.saved: set = set()                # profile assets that exist per slot

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
        Button(bar, text="Actions", command=self.actions_dialog,
               bg=CARD, fg=TEXT, activebackground="#333845", relief="flat",
               padx=12, pady=5).pack(side=RIGHT, padx=4, pady=10)
        Button(bar, text="Saved phones", command=self.saved_phones_dialog,
               bg=CARD, fg=TEXT, activebackground="#333845", relief="flat",
               padx=12, pady=5).pack(side=RIGHT, padx=4, pady=10)
        Button(bar, text="Run workflow", command=self.run_workflow_dialog,
               bg=CARD, fg=TEXT, activebackground="#333845", relief="flat",
               padx=12, pady=5).pack(side=RIGHT, padx=4, pady=10)
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
        sp = fetch_saved_profiles()
        if sp is not None:
            self.saved = {a["name"] for a in sp}
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
        if f"rfarm{name.split('-')[-1]}-data.tar.gz" in self.saved:
            Label(head, text="saved ·", fg=ACCENT, bg=CARD).pack(side=RIGHT)

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
        Button(btns, text="Save", command=lambda n=name: self.save_phone(n),
               bg="#333845", fg=ACCENT, relief="flat", padx=10).pack(side=LEFT)

    # -------------------------------------------------- saved-phone profiles
    def log(self, msg: str):
        self.log_box.insert("end", msg.rstrip("\n") + "\n")
        self.log_box.see("end")

    def save_phone(self, name):
        """Snapshot one phone's data volume and upload it (replaces in place)."""
        hp = self.farm.ssh_target()
        if not hp:
            messagebox.showerror("Save phone", "No live runner -- farm down?")
            return
        try:
            idx = int(name.split("-")[-1])
        except ValueError:
            return
        vol, host, port = f"rfarm{idx}-data", hp[0], hp[1]
        self.log(f"--- SAVE {name}: tar + upload {vol} (~600 MB, a minute or two) ---")
        self.status.set(f"saving {name} …")

        def work():
            r = subprocess.run(
                ["ssh", "-p", port, "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "BatchMode=yes", host,
                 f"bash ~/work/an-ci/an-ci/android-headless/redroid/"
                 f"snapshot-upload.sh save-{name} {vol}"],
                capture_output=True, text=True, timeout=900)
            out = (r.stdout + r.stderr).strip()
            self.root.after(0, lambda: self._save_done(name, r.returncode, out))
        threading.Thread(target=work, daemon=True).start()

    def _save_done(self, name, code, out):
        ok = code == 0 and "uploaded" in out
        self.log(f"[save {name}] "
                 f"{'OK -- profile replaced in farm-profiles' if ok else 'FAILED'}\n"
                 f"{out[-800:]}")
        self.status.set(f"{name} saved" if ok else f"{name} save FAILED (see log)")
        if ok:
            self.refresh()

    def saved_phones_dialog(self):
        dlg = Toplevel(self.root)
        dlg.title("Saved phones — farm-profiles (private repo)")
        dlg.configure(bg=PANEL)
        dlg.geometry("680x460")
        top = Frame(dlg, bg=PANEL)
        top.pack(fill=X)
        Label(top, text="SAVED PHONES", bg=PANEL, fg=TEXT,
              font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=12, pady=8)
        info = StringVar(value="loading …")
        Label(top, textvariable=info, bg=PANEL, fg=MUTED).pack(side=LEFT, padx=8)
        Button(top, text="Refresh", relief="flat", bg=CARD, fg=TEXT, padx=10,
               command=lambda: self._saved_fill(info)).pack(
               side=RIGHT, padx=10, pady=8)
        Label(dlg, text="One asset per phone-slot; every save REPLACES the old "
              "one (storage stays bounded). At farm boot each slot auto-restores "
              "its saved self -- delete a row to make that slot boot fresh.",
              bg=PANEL, fg=MUTED, anchor="w", justify=LEFT,
              wraplength=640).pack(fill=X, padx=12, pady=(0, 6))
        body = Frame(dlg, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self._sp_body = body
        self._saved_fill(info)

    def _saved_fill(self, info):
        body = getattr(self, "_sp_body", None)
        if body is None:
            return

        def work():
            assets = fetch_saved_profiles()
            self.root.after(0, lambda: self._saved_render(info, body, assets))
        threading.Thread(target=work, daemon=True).start()

    def _saved_render(self, info, body, assets):
        if assets is None:
            info.set("lookup failed (token / network)")
            assets = []
        else:
            total = sum(a.get("size", 0) for a in assets)
            info.set(f"{len(assets)} profile(s) · {total / 1e9:.2f} GB total"
                     if assets else "no profiles saved yet")
            self.saved = {a["name"] for a in assets}
        for w in body.winfo_children():
            w.destroy()
        if not assets:
            Label(body, text="Nothing here yet.\nUse a phone card's Save "
                  "button while a farm is live.", bg=BG, fg=MUTED,
                  pady=40).grid(row=0, column=0)
            return
        for c, h in enumerate(("phone-slot", "size", "saved (UTC)", "")):
            Label(body, text=h, bg=BG, fg=MUTED,
                  font=("Segoe UI", 9, "bold")).grid(
                  row=0, column=c, sticky="w", padx=8, pady=(6, 2))
        for r, a in enumerate(assets, start=1):
            Label(body, text=a.get("name", "?").replace("-data.tar.gz", ""),
                  bg=BG, fg=TEXT, font=("Consolas", 10)).grid(
                  row=r, column=0, sticky="w", padx=8)
            Label(body, text=f"{a.get('size', 0) / 1e6:.0f} MB", bg=BG,
                  fg=MUTED).grid(row=r, column=1, sticky="w", padx=8)
            Label(body, text=(a.get("updated_at", "") or "").replace("T", " ")[:16],
                  bg=BG, fg=MUTED).grid(row=r, column=2, sticky="w", padx=8)
            Button(body, text="Delete", relief="flat", bg=CARD, fg=WARN, padx=8,
                   command=lambda aa=a: self._saved_delete(aa, info)).grid(
                   row=r, column=3, sticky="e", padx=8)

    def _saved_delete(self, asset, info):
        if not messagebox.askyesno(
                "Delete profile",
                f"Delete {asset['name']} ({asset.get('size', 0) / 1e6:.0f} MB)?\n"
                "That phone-slot will boot FRESH at the next farm."):
            return

        def work():
            ok = delete_profile_asset(asset["id"], gh_token())

            def done():
                if ok:
                    messagebox.showinfo("Deleted", f"{asset['name']} deleted")
                else:
                    messagebox.showerror("Delete failed", "token / network?")
                self._saved_fill(info)
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------- actions (workflows)
    def actions_dialog(self):
        dlg = Toplevel(self.root)
        dlg.title("Actions — workflow runs (an-ci)")
        dlg.configure(bg=PANEL)
        dlg.geometry("780x520")
        top = Frame(dlg, bg=PANEL)
        top.pack(fill=X)
        Label(top, text="ACTIONS", bg=PANEL, fg=TEXT,
              font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=12, pady=8)
        info = StringVar(value="loading …")
        Label(top, textvariable=info, bg=PANEL, fg=MUTED).pack(side=LEFT, padx=8)
        Button(top, text="Refresh", relief="flat", bg=CARD, fg=TEXT, padx=10,
               command=lambda: self._runs_fill(info)).pack(
               side=RIGHT, padx=10, pady=8)
        dp = Frame(dlg, bg=PANEL)
        dp.pack(fill=X, padx=12, pady=(0, 6))
        Label(dp, text="start farm:", bg=PANEL, fg=MUTED).pack(side=LEFT)
        keyin = Entry(dp, show="•", width=36, bg="#111216", fg=TEXT,
                      relief="flat", insertbackground=TEXT)
        keyin.pack(side=LEFT, padx=6)
        ph, mi = StringVar(value="6"), StringVar(value="110")
        Label(dp, text="phones", bg=PANEL, fg=MUTED).pack(side=LEFT, padx=(8, 2))
        Entry(dp, textvariable=ph, width=3, bg="#111216", fg=TEXT,
              relief="flat", insertbackground=TEXT).pack(side=LEFT)
        Label(dp, text="minutes", bg=PANEL, fg=MUTED).pack(side=LEFT, padx=(8, 2))
        Entry(dp, textvariable=mi, width=4, bg="#111216", fg=TEXT,
              relief="flat", insertbackground=TEXT).pack(side=LEFT)
        Button(dp, text="Start farm", relief="flat", bg=ACCENT, fg="#0b0f0c",
               padx=10, font=("Segoe UI", 9, "bold"),
               command=lambda: self._farm_dispatch(
                   keyin.get().strip(), ph.get(), mi.get())).pack(
               side=LEFT, padx=10)
        body = Frame(dlg, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self._runs_body = body
        self._runs_info = info
        self._runs_fill(info)

    def _runs_fill(self, info):
        body = getattr(self, "_runs_body", None)
        if body is None:
            return

        def work():
            tok = gh_token()
            runs = gh_api(RUNS_API + "?per_page=12", tok).get(
                "workflow_runs", []) if tok else None
            self.root.after(
                0, lambda: self._runs_render(info, runs if tok else None))
        threading.Thread(target=work, daemon=True).start()

    def _runs_render(self, info, runs):
        body = self._runs_body
        for w in body.winfo_children():
            w.destroy()
        if runs is None:
            info.set("lookup failed (token / network)")
            return
        running = sum(1 for r in runs if r.get("status") == "in_progress")
        info.set(f"{len(runs)} recent runs · {running} running")
        if not runs:
            Label(body, text="no runs yet", bg=BG, fg=MUTED, pady=30).grid(
                row=0, column=0)
            return
        for c, h in enumerate(("run", "status", "started", "")):
            Label(body, text=h, bg=BG, fg=MUTED,
                  font=("Segoe UI", 9, "bold")).grid(
                  row=0, column=c, sticky="w", padx=8, pady=(6, 2))
        for r, run in enumerate(runs, start=1):
            Label(body, text=f"#{run.get('run_number')} {run.get('name', '?')}",
                  bg=BG, fg=TEXT, font=("Consolas", 10)).grid(
                  row=r, column=0, sticky="w", padx=8)
            st = run.get("status", "?")
            if st == "completed":
                st = run.get("conclusion") or "completed"
            Label(body, text=st, bg=BG,
                  fg=(ACCENT if st in ("in_progress", "queued", "success")
                      else WARN if st == "failure" else MUTED)).grid(
                  row=r, column=1, sticky="w", padx=8)
            Label(body, text=(run.get("created_at", "") or "").replace(
                "T", " ")[:16], bg=BG, fg=MUTED).grid(
                row=r, column=2, sticky="w", padx=8)
            if run.get("status") in ("in_progress", "queued", "pending"):
                Button(body, text="Stop", relief="flat", bg=CARD, fg=WARN,
                       padx=8, command=lambda rr=run: self._runs_cancel(rr)
                       ).grid(row=r, column=3, sticky="e", padx=8)

    def _runs_cancel(self, run):
        if not messagebox.askyesno("Stop run",
                f"Stop run #{run.get('run_number')} ({run.get('name')})?"):
            return

        def work():
            ok = gh_post(gh_token(), CANCEL_API.format(rid=run["id"]))
            def done():
                self.log(f"[actions] run #{run.get('run_number')} "
                         f"{'cancelled' if ok else 'cancel FAILED'}")
                if hasattr(self, "_runs_info"):
                    self._runs_fill(self._runs_info)
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _farm_dispatch(self, key, phones, minutes):
        if not key.startswith("tskey-"):
            messagebox.showerror(
                "Start farm", "Paste a Tailscale auth key (starts with tskey-).")
            return
        self.log(f"[actions] dispatching farm: {phones} phones, {minutes} min")

        def work():
            ok = gh_post(gh_token(), FARM_DISPATCH, {"ref": "main", "inputs": {
                "authkey": key, "phones": phones, "minutes": minutes}})
            self.root.after(0, lambda: self.log(
                f"[actions] farm dispatch {'sent' if ok else 'FAILED'}"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- run workflow
    def run_workflow_dialog(self):
        """Pick a JSON template from farm/templates + target phones -> run."""
        templates = sorted((REPO / "farm" / "templates").glob("*.json"))
        if not templates:
            messagebox.showinfo("No templates",
                                "Put JSON workflow templates in farm/templates/")
            return
        dlg = Toplevel(self.root)
        dlg.title("Run workflow")
        dlg.configure(bg=PANEL)
        Label(dlg, text="Template", bg=PANEL, fg=TEXT,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        lb = Listbox(dlg, height=min(6, len(templates)), bg="#111216", fg=TEXT,
                     relief="flat", selectbackground=ACCENT)
        for tp in templates:
            lb.insert("end", tp.name)
        lb.selection_set(0)
        lb.pack(fill=X, padx=12)
        Label(dlg, text="Phones", bg=PANEL, fg=TEXT,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        picks = []
        for ph in self.farm.phones:
            addr = ph.get("adb_address", "")
            if not addr:
                continue
            v = BooleanVar(value=True)
            picks.append((v, addr, ph["name"]))
            Checkbutton(dlg, text=f"{ph['name']}  ({ph.get('status')})",
                        variable=v, bg=PANEL, fg=TEXT,
                        selectcolor=CARD, activebackground=PANEL).pack(
                        anchor="w", padx=16)
        watch = BooleanVar(value=True)
        Checkbutton(dlg, text="Open live windows while running",
                    variable=watch, bg=PANEL, fg=TEXT, selectcolor=CARD,
                    activebackground=PANEL).pack(anchor="w", padx=16, pady=6)

        def go():
            sel = lb.curselection()
            addrs = [a for v, a, _ in picks if v.get()]
            if not sel or not addrs:
                messagebox.showerror("Run workflow",
                                     "Pick a template and at least one phone")
                return
            tpl = templates[sel[0]]
            dlg.destroy()
            self._run_workflow(tpl, addrs, watch.get())

        Button(dlg, text="Run", command=go, bg=ACCENT, fg="#0b0f0c",
               relief="flat", padx=18, pady=4,
               font=("Segoe UI", 10, "bold")).pack(pady=10)

    def _run_workflow(self, tpl: Path, addrs: list, watch: bool):
        if watch:
            for a in addrs:
                subprocess.Popen([launch.ensure_scrcpy(), "-s", a, "--no-audio",
                                  "--max-fps", "15", "--video-bit-rate", "2M"])
        out = REPO / "runs" / "rpa-app" / time.strftime("%Y%m%d-%H%M%S")
        cmd = [sys.executable, str(HERE / "rpa.py"), "--template", str(tpl),
               "--phones", ",".join(addrs), "--out", str(out)]
        banner = "\n--- RUN %s on %d phone(s) ---\n" % (tpl.name, len(addrs))
        self.log_box.insert("end", banner)
        self.log_box.see("end")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                cwd=str(REPO))

        def reader():
            for line in proc.stdout:
                line = line.rstrip("\n")
                self.root.after(0, lambda l=line + "\n":
                                (self.log_box.insert("end", l),
                                 self.log_box.see("end")))
        threading.Thread(target=reader, daemon=True).start()

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
                 "bash ~/work/an-ci/an-ci/android-headless/redroid/add-phone.sh"],
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
