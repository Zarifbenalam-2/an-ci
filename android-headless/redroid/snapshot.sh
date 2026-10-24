#!/usr/bin/env bash
# snapshot.sh -- run ON THE RUNNER (over the farm ssh) before the session
# ends. Tars every live phone's data volume so the phones can be reborn as
# THE SAME PHONES (apps, logins, settings) on the next runner:
#
#   ssh -p 2222 runner@<tailnet-ip> 'bash android-headless/redroid/snapshot.sh'
#   scp -P 2222 runner@<tailnet-ip>:profiles/rfarmN.tar.gz "D:/farm-profiles/"
#
# A stopped phone can be snapshotted too (docker volumes outlive their
# container until the machine dies -- which is exactly what this cheats).

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$REPO/profiles"
mkdir -p "$OUT"

mapfile -t vols < <(sudo docker volume ls --format '{{.Name}}' | grep '^rfarm.*-data$' || true)
[ "${#vols[@]}" -eq 0 ] && { echo "no rfarm volumes found"; exit 1; }

for v in "${vols[@]}"; do
  src="/var/lib/docker/volumes/$v/_data"
  [ -d "$src" ] || continue
  echo "snapshotting $v ..."
  # dalvik-cache regenerates on boot and app caches are junk -- excluding
  # them roughly halves the tarball with zero functional loss
  sudo tar czf "$OUT/$v.tar.gz" \
    --exclude='./dalvik-cache' --exclude='*/cache' \
    -C "$src" .
  sudo chmod 644 "$OUT/$v.tar.gz"
  echo "  -> $OUT/$v.tar.gz ($(du -h "$OUT/$v.tar.gz" | cut -f1))"
done

echo "done. pull them to the PC before the session ends:"
echo "  scp -P 2222 runner@\$(tailscale ip -4 | head -1):profiles/rfarmN.tar.gz D:/farm-profiles/"
