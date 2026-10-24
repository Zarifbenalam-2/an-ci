#!/usr/bin/env python3
"""Update .session/redroid-farm.json for one redroid phone (farm workflow).

    python3 farm_redroid_status.py <phone-index> <state> <tailnet-ip> <count>

Phones are phone-1..N, adb published by docker on tailnet port 21000+index.
"""
import json
import os
import sys
import time
from pathlib import Path

STATUS = Path(__file__).resolve().parents[2] / ".session" / "redroid-farm.json"
PORT_BASE = 21000


def main() -> int:
    i, state, tsip, n = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
    try:
        phones = json.loads(STATUS.read_text(encoding="utf-8"))["phones"]
    except Exception:
        phones = [{"name": f"phone-{k}", "port": PORT_BASE + k,
                   "status": "pending", "gsf_id": ""}
                  for k in range(1, n + 1)]
    port = PORT_BASE + i
    for p in phones:
        if p["name"] == f"phone-{i}":
            p["status"] = state
            p["adb_address"] = f"{tsip}:{port}" if tsip else ""
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(
        {"run": os.environ.get("GITHUB_RUN_ID", ""),
         "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
         "ssh": os.environ.get("FARM_SSH", ""),
         "phones": phones}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
