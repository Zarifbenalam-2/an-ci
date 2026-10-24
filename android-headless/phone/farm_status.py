#!/usr/bin/env python3
"""Update .session/farm-status.json for one phone (used by phone-farm.yml).

    python3 farm_status.py <phone-index> <state> <tailnet-ip> <phone-count>

Writes/updates a JSON file the PC-side app can read straight out of the repo:

    {"run": "39695517657", "updated": "...", "phones": [
        {"name": "phone-1", "console": 5554, "adb": 5555,
         "status": "ready", "adb_address": "100.x.y.z:5555"}, ...]}
"""
import json
import os
import sys
import time
from pathlib import Path

STATUS = Path(__file__).resolve().parents[2] / ".session" / "farm-status.json"


def main() -> int:
    i, state, tsip, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    i, n = int(i), int(n)
    try:
        phones = json.loads(STATUS.read_text(encoding="utf-8"))["phones"]
    except Exception:
        phones = [{"name": f"phone-{k}", "console": 5552 + 2 * k,
                   "adb": 5553 + 2 * k, "status": "pending"}
                  for k in range(1, n + 1)]
    adb_port = 5553 + 2 * i
    for p in phones:
        if p["name"] == f"phone-{i}":
            p["status"] = state
            p["adb_address"] = f"{tsip}:{adb_port}" if tsip else ""
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(
        {"run": os.environ.get("GITHUB_RUN_ID", ""),
         "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
         "phones": phones}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
