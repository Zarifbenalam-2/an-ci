# android-headless

A real Android device you control from Python — no Android Studio, no GUI, no browser session,
no third-party account. This is the legitimate equivalent of "drive the phone programmatically":
an Android Virtual Device (AVD) on your own machine, reached through `adb`.

## Verified working

The CI workflow has been run green end to end on a stock `ubuntu-latest` runner. What a run
produces, from the evidence it commits to `diagnostics/`:

| Check | Observed |
| --- | --- |
| Emulator boot | `Boot completed in 38492 ms`, device `emulator-5554` online |
| KVM acceleration | `/dev/kvm` present, 4 virt flags — hardware accelerated, not software |
| Screen size | `adbctl.py size` → `1080x2400` |
| App launched | `topResumedActivity=... com.android.settings/.Settings` |
| Input delivered | swipe → `mCurrentFocus=Window{... NotificationShade}` |
| Screenshots | `01-launch.png`, `02-shade.png` — real 1080x2400 PNGs, ~147 KB and ~133 KB |
| Screen recording | `run.mp4`, 361 KB |
| Device log | `logcat.txt`, ~1.3 MB |

Every run appends a report to `diagnostics/run-<id>.txt` with file sizes, checksums, PNG
dimensions, the device's focused window and the raw device log — so a run is verifiable without
logging into GitHub. That path is deliberately excluded from the workflow's `push` filter, so
these commits cannot retrigger the workflow. Delete the folder any time; it is only evidence.

## Why this instead of hot-wiring AI Studio

The AI Studio virtual-device preview is an internal RPC surface tied to a live browser login with
rotating, device-bound tokens. Driving it headlessly means impersonating your own session, which
(a) breaks whenever Google ships, (b) burns your AI Studio quota, and (c) risks the account.
An emulator you own has none of those properties and is scriptable end to end.

## Requirements

| Need | Why | Check |
| --- | --- | --- |
| JDK 17+ | `sdkmanager` / `avdmanager` are Java tools | `java -version` |
| ~10 GB free disk | system image + emulator + AVD | — |
| Virtualization on | Intel VT-x / AMD-V in BIOS, WHPX enabled | Task Manager → Performance → CPU → "Virtualization: Enabled" |
| 8 GB+ RAM | 4 GB goes to the AVD | — |

Python and Node are already present.

## Install

```bash
python setup_sdk.py
```

This downloads the Android command-line tools and platform-tools, accepts the SDK licences,
installs the emulator plus an x86_64 system image, and creates an AVD named `headless`.
Override with env vars if you like: `ANDROID_API=35 AVD_NAME=test python setup_sdk.py`.

Nothing is installed outside `ANDROID_HOME` (default `%LOCALAPPDATA%\Android\Sdk`), so deleting
that folder undoes everything.

## Run

```bash
# 1. boot it headless (no window, software GPU, no audio)
python adbctl.py boot

# 2. confirm it is up
python adbctl.py wait
python adbctl.py shot frame.png

# 3. drive it
python adbctl.py tap 540 1200
python adbctl.py text "hello world"
python adbctl.py key 4                 # KEYCODE_BACK
python adbctl.py open com.google.android.youtube
python adbctl.py shell "dumpsys battery"

# 4. stop
python adbctl.py stop
```

Prefer to *see* the device while scripting it? `python adbctl.py show` launches `scrcpy`
if you install it — a window mirroring the same headless device, so the automation and your
eyes share one screen.

## Using it from your own code

```python
from adbctl import AndroidDevice

dev = AndroidDevice()
dev.wait_for_boot(timeout=180)
dev.start_app("com.android.settings")
dev.tap(540, 1200)
dev.swipe(540, 1600, 540, 600, duration_ms=300)
png = dev.screenshot_bytes()          # raw PNG, no temp file
print(dev.shell("getprop ro.build.version.release"))
```

Every call is a plain `subprocess` invocation of `adb`, so anything `adb` can do, you can do.

## Cloud options (nothing installed locally)

Ranked by how well each matches "a real Android device I drive from code".

- **GitHub Actions** — two workflows. `android-device.yml` boots a
  hardware-accelerated AVD on a stock `ubuntu-latest` runner (4 vCPU / 16 GB,
  `/dev/kvm` exposed), drives your app, and returns screenshots, screen
  recording and `logcat` as artifacts. Free on public repos, nothing installed
  locally. `android-debug.yml` does the same but holds the emulator open and
  gives you an SSH session into the runner, locked to you, so you can tap the
  device and read its errors live while debugging a run. Both are bounded by the
  6-hour job ceiling and by GitHub's Acceptable Use Policies: use them to build
  and test the app in the repo — running your app's CI is exactly what they are
  for, using the runner as a general-purpose box is what gets accounts banned.
- **Firebase Test Lab** — Google-hosted virtual and physical devices with a real
  API and a free daily allowance on Spark. Same batch shape as Actions, but
  Google runs the hardware. Best when you want many device models.
- **Oracle Cloud Always Free / GCP free tier** — a persistent VM you install this
  toolkit on, giving a long-lived emulator reachable over `adb connect`. This is
  the only option here that gives you a *live* phone. Oracle's ARM shape
  (4 cores / 24 GB) works; GCP's free `e2-micro` (1 GB) does not. See the
  bootstrap commands in the project notes.
- **A cheap used physical phone + `adb` over Wi-Fi** — faster and more faithful
  than any emulator, and the most reliable if you need real app behaviour.

## A note on what you run on it

Automating an app you own is fine. Using this to generate artificial traffic, views, or
engagement on YouTube or anywhere else violates that platform's terms and is easy to detect —
worth keeping on the right side of, given an emulator like this leaves a clean audit trail.
