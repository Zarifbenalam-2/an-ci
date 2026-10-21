# Architecture: how you actually get a phone in a window

## The question you asked

You want one button that spawns a phone and gives you a live window you control with your mouse,
where you can drop an APK in from your PC and pull files back out. That is a real, achievable
thing — but it is **not** a GitHub Actions thing, and it is worth being blunt about why.

**GitHub Actions cannot be the host for this.** Two hard walls: a job dies at six hours and then
the machine is destroyed, and runners sit behind NAT with no inbound route, so nothing on your PC
can connect *in* to hold a window open. Actions is excellent at "run my app and hand me back the
evidence" — that is what the two workflows in this repo do, and they are verified working. It is
the wrong tool for "a phone that is just… there, that I poke with a mouse."

So the live-window product needs a **persistent host**. That host is a machine that stays up.

## The access model

This is the part that matters, and it answers your "will there be proper adb access" question
directly: **yes, and it is the same single connection that serves both you and me.**

```
   ┌──────────────────────────────┐
   │  Host: Android emulator      │     stays up, persists between sessions
   │  adbd listening on 5555      │
   └──────────────┬───────────────┘
                  │  ssh -L 5555:localhost:5555
                  ▼
   ┌──────────────────────────────┐
   │  Your PC: localhost:5555     │     adb sees it as a plugged-in device
   └───────┬──────────────┬───────┘
           │              │
     scrcpy window    adbctl.py / me
     (your eyes,      (scripted taps, logcat,
      your mouse)      APK install, file moves)
```

The tunnel publishes the remote emulator at `localhost:5555` **on your machine**. adb then treats
it exactly like a phone on a USB cable. Two consumers read that one device concurrently:

- **scrcpy** mirrors the screen (H.264 over adb) and sends your mouse and keyboard back. This is
  the "real phone in my window" part, and it is what BlueStacks-style tools are built on.
- **adb** — anything at all: `adbctl.py` for scripted control, or *me*, running commands against
  the same serial. That is how I control it in future: not by reaching into your session, but
  because the device is exposed locally and I can address it like any other.

Nothing about this requires me to hold a credential of yours. The device is just there.

## One button

`phone/launch.py` is that button. It handles the whole chain:

| Step | What it does |
| --- | --- |
| Provision | Downloads `adb` (platform-tools) and `scrcpy` into `phone/tools/` — portable copies, no system install, no admin rights |
| Start | Remote mode: asks the VM over ssh to boot the emulator, but only if it is not already running. Local mode: boots the AVD itself |
| Connect | Opens the ssh tunnel, then `adb connect localhost:5555`, and waits for the framework to finish booting |
| Show | Launches scrcpy — the live window, with a real adb session behind it |

```bash
python phone/launch.py local                        # emulator on this PC
python phone/launch.py remote --vm ubuntu@1.2.3.4   # emulator on a cloud VM
```

There is no `.bat` file to double-click because a shortcut to that same command does the job.

## Moving files both ways

| Direction | Command |
| --- | --- |
| APK in, and installed | `adbctl.py install-apk app.apk` |
| Any file in | `adbctl.py push photo.jpg` (defaults to `/sdcard/Download/`) |
| File out | `adbctl.py pull /sdcard/Download/x.png .` |

With the scrcpy window open you can also drag a file onto it and scrcpy pushes it to the device,
which is the copy-paste feel you described. Pulling *out* is the `pull` command above — there is
no drag-out-of-the-window gesture in scrcpy, which is a real limitation worth knowing up front.

## Which host to use

| Host | Live window | Persistence | Cost | Honest downside |
| --- | --- | --- | --- | --- |
| **Your own PC** | Best — no network hop | Forever | Free | Needs the ~10 GB Android SDK install locally. Fastest by far, and the closest to the BlueStacks feel you described |
| **Free cloud VM (Oracle Always Free)** | Good | Forever | Free | No nested virtualization on the free ARM shape, so software rendering; expect visible latency and no smooth video |
| **GitHub Actions** | Cannot | 6 hours max | Free | No inbound network — a window is impossible in principle |

The recommendation: **if the goal is the BlueStacks feel, run the emulator locally.** The "nothing
installed on my PC" constraint and the "feels like it runs on my device" requirement pull in
opposite directions, and latency is the thing you will actually notice. A local AVD plus scrcpy is
the same stack, one hop shorter. Use the cloud VM when you specifically need a phone that is alive
while your PC is off, and accept the softness that comes with it.

## What is emulator, and what is a "real" cloud phone

Cloud-phone services hand you an actual handset in a rack. That buys fidelity — real GPU, real
Play certification, no emulator detection — and costs money per minute. An emulator buys free,
disposable, infinitely clonable devices and full adb access. For building and testing your own app,
the emulator wins outright. If you later hit an app that refuses to run on an emulator, that is the
signal to pay for a real cloud phone, not before.

## What exists vs what you still need to create

**Built and verified:** the device toolkit, the two CI workflows, the launcher, file transfer, and
the live-view path.

**Yours to create:** the host itself. There is no VM yet — you need to sign up for the Oracle
Always Free account and run `cloud/provision.sh` on it once, or run `setup_sdk.py` locally. That is
the one step I cannot do for you, because it needs an account of yours.
