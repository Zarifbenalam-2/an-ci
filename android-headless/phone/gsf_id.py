#!/usr/bin/env python3
"""Print a phone's GSF Android-ID (needed to register the device at
https://www.google.com/android/uncertified so Google sign-in works on an
uncertified/emulated device).

    python3 gsf_id.py <adb-path> <serial>

Pulls the gservices.db over adb (root) and reads android_id locally.
Prints UNKNOWN if the database does not exist yet (Play Services has not
finished provisioning on first boot -- retry a minute later).
"""
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    adb, serial = sys.argv[1], sys.argv[2]
    db = "data/data/com.google.android.gsf/databases/gservices.db"
    subprocess.run([adb, "-s", serial, "root"], capture_output=True)
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "gservices.db"
        r = subprocess.run([adb, "-s", serial, "pull", f"/{db}", str(local)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not local.exists():
            print("UNKNOWN")
            return 0
        con = sqlite3.connect(local)
        try:
            row = con.execute(
                "SELECT value FROM main WHERE name = 'android_id'").fetchone()
            print(row[0] if row else "UNKNOWN")
        except sqlite3.Error:
            print("UNKNOWN")
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
