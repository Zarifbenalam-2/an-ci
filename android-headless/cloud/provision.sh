#!/usr/bin/env bash
# Provision a persistent, headless Android device on a fresh Ubuntu VM and leave
# it running so you can drive it over adb from your own machine.
#
# Intended shape: Oracle Cloud Always Free, Ampere A1, 4 cores / 24 GB, Ubuntu 24.04 ARM.
# GCP's free e2-micro (1 GB) is too small for a modern AVD; don't bother.
#
# Usage on the VM:   bash provision.sh
# Then from your PC: ssh -N -L 5555:localhost:5555 ubuntu@<vm-ip>
#                    adb connect localhost:5555

set -euo pipefail

ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
API="${ANDROID_API:-34}"
ABI="${ANDROID_ABI:-arm64-v8a}"
IMAGE="system-images;android-${API};google_apis;${ABI}"
AVD="${AVD_NAME:-headless}"
CLT_ZIP="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"

log() { printf '\n==> %s\n' "$*"; }

log "packages"
sudo apt-get update -qq
sudo apt-get install -y -qq openjdk-17-jdk unzip curl

log "command-line tools -> $ANDROID_HOME"
mkdir -p "$ANDROID_HOME/cmdline-tools"
if [ ! -x "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ]; then
  curl -fsSL -o /tmp/clt.zip "$CLT_ZIP"
  unzip -q -o /tmp/clt.zip -d "$ANDROID_HOME/cmdline-tools"
  rm -rf "$ANDROID_HOME/cmdline-tools/latest"
  mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
fi

export ANDROID_HOME
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

log "sdk packages (this pulls a few GB)"
yes | sdkmanager --licenses >/dev/null 2>&1 || true
sdkmanager --install platform-tools emulator "$IMAGE"

log "create AVD '$AVD'"
if ! avdmanager list avd 2>/dev/null | grep -q "Name: $AVD"; then
  echo no | avdmanager create avd -n "$AVD" -k "$IMAGE" --device pixel_6 --force
fi

# Persist the environment for future SSH sessions.
if ! grep -q 'ANDROID_HOME' "$HOME/.bashrc" 2>/dev/null; then
  cat >>"$HOME/.bashrc" <<EOF

export ANDROID_HOME="$ANDROID_HOME"
export PATH="\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/emulator:\$PATH"
EOF
fi

log "boot headless"
pkill -f "emulator -avd" 2>/dev/null || true
nohup emulator -avd "$AVD" -no-window -no-audio -no-boot-anim -no-snapshot \
  -gpu swiftshader_indirect >"$HOME/emulator.log" 2>&1 &

log "waiting for boot (first boot on ARM is slow)"
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
  sleep 3
done
for scale in window_animation_scale transition_animation_scale animator_duration_scale; do
  adb shell settings put global "$scale" 0.0 >/dev/null 2>&1 || true
done

cat <<'DONE'

Device is up, and it keeps running between your SSH sessions.

On your own machine:

  ssh -N -L 5555:localhost:5555 ubuntu@<vm-ip>     # leave this open
  adb connect localhost:5555                       # from another terminal
  python android-headless/adbctl.py --serial localhost:5555 shot frame.png

After that, drive it exactly as if it were plugged into your PC.
Notes: free-tier ARM has no nested virtualization, so this is software
rendering -- fine for scripted app control, not for smooth video.
DONE
