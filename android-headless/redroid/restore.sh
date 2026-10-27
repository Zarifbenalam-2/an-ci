#!/usr/bin/env bash
# restore.sh -- run ON THE RUNNER (over the farm ssh) right after a farm is
# up, with tarballs previously pulled FROM the PC:
#
#   scp -P 2222 D:/farm-profiles/rfarmN.tar.gz runner@<tailnet-ip>:profiles/
#   ssh -p 2222 runner@<ip> 'bash android-headless/redroid/restore.sh'
#
# Puts each tarball back into its phone's data volume and restarts that
# phone so Android boots with its old self. Phones without a tarball keep
# their fresh state.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
IN="$REPO/profiles"

shopt -s nullglob
tars=("$IN"/rfarm*.tar.gz)
[ "${#tars[@]}" -eq 0 ] && { echo "no tarballs in $IN -- nothing to restore"; exit 0; }

for t in "${tars[@]}"; do
  v="$(basename "$t" .tar.gz)"              # e.g. rfarm3-data
  idx="${v%%-*}"                            # rfarm3
  # never guess the host path, and only root can see /var/lib/docker --
  # a plain [ -d ] reports "not a dir" on permission denial
  dst="$(sudo docker volume inspect "$v" --format '{{.Mountpoint}}' 2>/dev/null || true)"
  if [ -z "$dst" ] || ! sudo test -d "$dst"; then
    echo "$v: volume does not exist on this runner (phone index not running?) -- skipping"
    continue
  fi
  echo "restoring $v ..."
  sudo docker rm -f "$idx" >/dev/null 2>&1 || true
  sudo rm -rf "$dst"
  sudo mkdir -p "$dst"
  sudo tar xzf "$t" -C "$dst"
  # restart the phone on the same port binding as the farm uses
  port=$((21000 + ${idx#rfarm}))
  . "$REPO/.session/farm-env" 2>/dev/null || true
  tsip="${FARM_TSIP:?FARM_TSIP missing -- run inside a live farm session}"
  sudo docker run -d --name "$idx" --privileged \
    --memory 3g --shm-size 1g \
    -p "$tsip:$port:5555" \
    -v "$v:/data" \
    "${FARM_IMAGE:-redroid13-gapps}" \
    androidboot.redroid_width=720 androidboot.redroid_height=1600 \
    androidboot.redroid_dpi=280 androidboot.use_memfd=true \
    ro.setupwizard.mode=DISABLED
  echo "  -> $idx restarted at $tsip:$port with its old data"
done
echo "done. give each phone ~1 min to boot, then reconnect adb."
