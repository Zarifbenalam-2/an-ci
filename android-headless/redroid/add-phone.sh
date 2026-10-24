#!/usr/bin/env bash
# add-phone.sh -- run ON THE RUNNER (over the farm's ssh) to boot one more
# phone on the same machine. This is the "fill the runner before opening a
# new one" mechanism: no new workflow dispatch, the phone comes up next to
# the existing ones and is published on the same tailnet address.
#
#   ssh -p 2222 runner@<tailnet-ip> 'bash android-headless/redroid/add-phone.sh'
#
# Refuses politely when the runner is already at capacity. Everything it
# needs (docker image, tailnet ip, repo checkout) already exists on the
# runner because the farm workflow put it there.

set -euo pipefail

CAPACITY="${FARM_CAPACITY:-10}"          # hard cap this script enforces
PORT_BASE="${FARM_PORT_BASE:-21000}"     # phone adb ports start here
REPO="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root (for .session)

cd "$REPO"
# farm-env is written by the workflow: FARM_TSIP / FARM_IMAGE / FARM_CAPACITY
[ -f .session/farm-env ] && . ./.session/farm-env

# current phone count = highest used index in .session/redroid-farm.json
COUNT=$(python3 -c "
import json
try:
    phones = json.load(open('.session/redroid-farm.json'))['phones']
    print(max(int(p['name'].split('-')[1]) for p in phones))
except Exception:
    print(0)
")

if [ "$COUNT" -ge "$CAPACITY" ]; then
  echo "runner already at capacity ($COUNT/$CAPACITY) -- spawn a new runner instead"
  exit 3
fi

I=$((COUNT + 1))
PORT=$((PORT_BASE + I))
TSIP="${FARM_TSIP:?FARM_TSIP must be exported by the farm workflow}"
IMAGE="${FARM_IMAGE:-redroid13-gapps}"

echo "booting phone-$I on $TSIP:$PORT ..."
sudo docker run -d --name "rfarm$I" --privileged \
  --memory 3g --shm-size 1g \
  -p "$TSIP:$PORT:5555" \
  -v "rfarm$I-data:/data" \
  "$IMAGE" \
  androidboot.redroid_width=720 androidboot.redroid_height=1600 \
  androidboot.redroid_dpi=280 androidboot.use_memfd=true \
  ro.setupwizard.mode=DISABLED

ADB="$(command -v adb || echo "$RUNNER_TEMP/pt/platform-tools/adb")"
export PATH="$(dirname "$ADB"):$PATH"

# wait for adbd + boot
ok=no
for w in $(seq 1 60); do
  adb connect "$TSIP:$PORT" >/dev/null 2>&1 || true
  v=$(adb -s "$TSIP:$PORT" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
  [ "$v" = "1" ] && { ok=yes; break; }
  sleep 4
done
[ "$ok" = yes ] || { echo "phone-$I failed to boot"; exit 1; }

for s in window_animation_scale transition_animation_scale animator_duration_scale; do
  adb -s "$TSIP:$PORT" shell settings put global "$s" 0.0 || true
done
adb -s "$TSIP:$PORT" shell settings put secure navigation_mode 0 || true

python3 android-headless/phone/farm_redroid_status.py "$I" "ready" "$TSIP" "$((COUNT + 1))"

git config user.name  "an-ci bot"
git config user.email "actions@users.noreply.github.com"
git add .session >/dev/null 2>&1 || true
git -c commit.gpgsign=false commit -q -m "redroid farm: phone-$I added over ssh" || true
for a in 1 2 3; do
  git fetch -q origin main && git rebase -q origin/main \
    && git push -q origin HEAD:main && break
  sleep 3
done

echo "phone-$I READY at $TSIP:$PORT ($((COUNT + 1))/$CAPACITY)"
