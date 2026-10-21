# Tailscale — setup, reuse, and how the live device uses it

The `android-live` workflow publishes the Android device to a **Tailscale
tailnet**. `Tailscale.cmd` (double-click, like `Phone.cmd`) does everything
the PC side needs, and can be re-run any number of times.

## What Tailscale.cmd does

1. **Suppresses new System Restore points** — sets
   `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore\SystemRestorePointCreationFrequency`
   to `0x7fffffff`. Any installer that asks Windows to create a restore point
   then fails instantly, using no disk space. System Restore itself and old
   restore points are untouched. **Revert:** in an admin terminal,
   `reg delete "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore" /v SystemRestorePointCreationFrequency /f`
2. **Installs Tailscale silently to `E:\Tailscale`** (or `C:\` if there is no
   E: drive), downloading the official installer from
   `pkgs.tailscale.com`. Skipped if already installed. One UAC prompt.
3. **Starts login** — opens `tailscale login`, which prints a URL; sign in
   with Google/Microsoft/GitHub. Only needed once per machine.
4. Prints the follow-up: create an auth key at
   <https://login.tailscale.com/admin/settings/keys>
   (tick **Reusable** and **Ephemeral nodes**) and add it as the repo secret
   `TS_AUTHKEY` at
   <https://github.com/Zarifbenalam-2/an-ci/settings/secrets/actions>.

## Why the runner needs it

GitHub runners are behind a NAT; nothing can dial into them. The
`android-live` workflow joins the runner to *your* tailnet with the auth key,
so the runner and your PC share a private network. It then relays the
emulator's adb port onto the tailnet:

```
socat TCP-LISTEN:5555,fork,reuseaddr,bind=<runner-tailscale-ip> TCP:127.0.0.1:5555
```

Your PC then does `adb connect <runner-tailscale-ip>:5555` and `scrcpy`
gives you a live, mouse-controllable phone window. Traffic never leaves the
tailnet, and `--ephemeral` means the runner node vanishes when the job ends.

## Files

- `Tailscale.cmd` — the one-button entry point (safe to double-click).
- `tailscale-setup.ps1` — the actual logic, kept visible and editable.

## Manual equivalents

Install: `tailscale-setup-full-latest.exe /quiet INSTALLDIR="E:\Tailscale"`
Login: `tailscale login` (prints a URL, opens a browser)
Re-enable restore points: delete the registry value named above.
