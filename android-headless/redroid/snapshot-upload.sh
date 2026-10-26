#!/usr/bin/env bash
# snapshot-upload.sh -- run ON THE RUNNER to tar every phone's data volume
# and upload it to the private farm-profiles release. Called by the farm
# workflow twice: once right after provisioning (the reliable moment) and
# once more at session end (best effort -- docker can be flaky there).
#
#   PROFILE_TOKEN=... bash snapshot-upload.sh [beacon-label]
#
# UNKILLABLE BY DESIGN: no set -e, every failure logged loudly, always exits 0.

LABEL="${1:-snapshot}"
LOG="${FARM_LOG:-/tmp/farm.log}"
PA="Authorization: Bearer ${PROFILE_TOKEN:?PROFILE_TOKEN not set}"
PAPI="https://api.github.com/repos/Zarifbenalam-2/farm-profiles/releases"
PDIR="${RUNNER_TEMP:-/tmp}/profiles"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

say() { echo "[snapshot:$LABEL] $*" | tee -a "$LOG"; }

beacon() {
  git -C "$REPO" add .session >/dev/null 2>&1
  git -C "$REPO" -c commit.gpgsign=false commit -q -m "rfarm beacon: $1" >/dev/null 2>&1
  for a in 1 2 3; do
    git -C "$REPO" fetch -q origin main \
      && git -C "$REPO" rebase -q --autostash origin/main \
      && git -C "$REPO" push -q origin HEAD:main && return 0
    sleep 3
  done
  return 0
}

mkdir -p "$PDIR"
VOLS="$(sudo docker volume ls --format '{{.Name}}' 2>&1 | tr '\n' ' ')"
say "volumes seen: [$VOLS]"

staged=0
for pv in $(echo "$VOLS" | tr ' ' '\n' | grep '^rfarm.*-data$'); do
  # never guess the host path: ask docker where the volume lives, and test
  # the dir with sudo -- /var/lib/docker is root-only, so a plain [ -d ]
  # reports "not a dir" on permission denial (that was the staged-0 bug)
  psrc="$(sudo docker volume inspect "$pv" --format '{{.Mountpoint}}' 2>/dev/null)"
  sudo test -d "$psrc" || { say "SKIP $pv: mountpoint '$psrc' not readable even via sudo"; continue; }
  sudo tar czf "$PDIR/$pv.tar.gz" \
    --exclude='./dalvik-cache' --exclude='*/cache' \
    -C "$psrc" . >/tmp/tar.err 2>&1 \
    || { say "TAR FAILED $pv: $(tail -2 /tmp/tar.err | tr '\n' ' ')"; continue; }
  sudo chmod 644 "$PDIR/$pv.tar.gz"
  say "snapshotted $pv ($(du -h "$PDIR/$pv.tar.gz" | cut -f1))"
  staged=$((staged + 1))
done
say "staged $staged tarball(s)"

[ "$staged" -gt 0 ] || { say "nothing to upload"; beacon "snapshot($LABEL): 0 tarballs"; exit 0; }

prid="$(curl -fsS -H "$PA" "$PAPI/tags/profiles" 2>>"$LOG" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null)"
if [ -z "$prid" ]; then
  prid="$(curl -fsS -X POST -H "$PA" -H "Content-Type: application/json" \
    -d '{"tag_name":"profiles","name":"phone profiles"}' "$PAPI" 2>>"$LOG" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null)"
fi
[ -n "$prid" ] || { say "RELEASE LOOKUP/CREATE FAILED"; beacon "snapshot($LABEL) FAILED: no release"; exit 0; }

uploaded=0
for pf in "$PDIR"/*.tar.gz; do
  [ -f "$pf" ] || continue
  pn="$(basename "$pf")"
  aid="$(curl -fsS -H "$PA" "$PAPI/$prid/assets" 2>>"$LOG" \
    | python3 -c 'import json,sys;print(next((a["id"] for a in json.load(sys.stdin) if a["name"]==sys.argv[1]),""))' "$pn" 2>/dev/null)"
  [ -n "$aid" ] && curl -fsS -X DELETE -H "$PA" "$PAPI/$prid/assets/$aid" >/dev/null 2>&1
  if curl -fsS -X POST -H "$PA" -H "Content-Type: application/octet-stream" \
       --data-binary "@$pf" "$PAPI/$prid/assets?name=$pn" >>"$LOG" 2>&1; then
    say "uploaded $pn"; uploaded=$((uploaded + 1))
  else
    say "UPLOAD FAILED $pn"
  fi
done
beacon "snapshot($LABEL): $uploaded/$staged profiles uploaded"
exit 0
