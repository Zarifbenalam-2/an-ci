#!/usr/bin/env python3
"""Summarize a redroid-stress CSV (used by redroid-stress.yml).

    python3 stress_summary.py diagnostics/redroid-stress-8.csv

Prints per-phone idle/peak/avg memory and CPU, death detection, and host
pressure, so the workflow can append it straight to the results file.
"""
import csv
import sys


def to_mb(s: str) -> float:
    s = s.split("/")[0].split()[0].strip()
    if s.endswith("GiB"):
        return float(s[:-3]) * 1024
    if s.endswith("MiB"):
        return float(s[:-3])
    return 0.0


def main() -> int:
    path = sys.argv[1]
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh) if len(r) == 5][1:]

    names = sorted({r[1] for r in rows if r[1] != "HOST"})
    print("== per-phone summary ==")
    for name in names:
        rs = [r for r in rows if r[1] == name]
        dead = any(r[2] != "running" for r in rs)
        mems = [to_mb(r[3]) for r in rs if r[3]]
        cpus = []
        for r in rs:
            try:
                cpus.append(float(r[4].rstrip("%")))
            except ValueError:
                pass
        idle = [to_mb(r[3]) for r in rs[:18]]
        idle_avg = sum(idle) / max(1, len(idle))
        peak = max(mems) if mems else 0.0
        avg_cpu = sum(cpus) / max(1, len(cpus))
        peak_cpu = max(cpus) if cpus else 0.0
        state = "DIED/RESTARTED" if dead else "alive"
        print(f"{name}: idle~{idle_avg:.0f}MB  peak={peak:.0f}MB  "
              f"avgCPU={avg_cpu:.0f}%  peakCPU={peak_cpu:.0f}%  {state}")

    host = [r for r in rows if r[1] == "HOST"]
    if host:
        print(f"host samples: {len(host)} · last: {host[-1][3]} · load {host[-1][4]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
