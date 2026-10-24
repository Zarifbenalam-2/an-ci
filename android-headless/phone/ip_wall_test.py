#!/usr/bin/env python3
"""ip_wall_test.py — does THIS network's egress IP get YouTube's bot wall?

The experiment, in one paragraph:
  YouTube's watch page embeds a JSON verdict called playabilityStatus. Fetch
  the page as a plain mobile browser -- no login, no app, no device identity,
  fresh cookies just to pass the consent redirect -- and read that verdict.
  Run the exact same request from two different networks and the ONLY
  variable left is the source IP. If one network gets OK and the other gets
  LOGIN_REQUIRED ("Sign in to confirm you're not a bot"), the trigger is IP
  reputation, not the device, app, or registration.

  On your PC (residential IP):   status=OK          -> plays
  On a GitHub runner (Azure IP): status=LOGIN_REQUIRED -> the bot wall

Usage:
  python ip_wall_test.py                          # test this machine
  python ip_wall_test.py --label "my pc"
  python ip_wall_test.py --via-ssh runner@100.x.y.z --ssh-port 2222
                                                   # run the same test ON a
                                                   # farm runner over the
                                                   # tailnet, using THIS file

Honest limits: one video, one moment; YouTube risk-scores per request, so
edge-case IPs can flip between runs. This measures the anonymous-web
verdict -- a signed-in session is Google's own offered remedy and is not
what this tool measures.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import re
import subprocess
import sys
import urllib.request

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 "
      "Mobile Safari/537.36")
DEFAULT_VIDEO = "https://www.youtube.com/watch?v=vgm306Ha0lw"


def fetch(url: str, jar: http.cookiejar.CookieJar) -> tuple[str, str, str]:
    """Return (final_url, egress_ip_seen_by_server_not_needed, html).

    The cookie jar carries the consent cookies through YouTube's redirect
    chain, exactly like a browser's first visit.
    """
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with opener.open(req, timeout=40) as resp:
        return resp.geturl(), str(resp.status), resp.read().decode("utf-8", "replace")


def playability(html: str) -> tuple[str, str]:
    """Extract (status, reason) from the embedded ytInitialPlayerResponse."""
    # the page embeds the JSON with escaped quotes (\"), so normalise first
    flat = html.replace('\\"', '"')
    m = re.search(r'"playabilityStatus":\s*\{.{0,600}?"status"\s*:\s*"([A-Z_]+)"',
                  flat, re.S)
    if not m:
        return "UNKNOWN", "no playabilityStatus in page (layout changed?)"
    status = m.group(1)
    r = re.search(r'"reason"\s*:\s*"([^"]{0,120})', flat[m.start():m.start() + 600])
    return status, (r.group(1) if r else "")


def run_test(label: str, video: str) -> int:
    jar = http.cookiejar.CookieJar()
    _, _, ip_html = fetch("https://api.ipify.org", jar)
    egress = ip_html.strip()
    final_url, code, html = fetch(video, jar)
    status, reason = playability(html)

    print(f"[{label}]")
    print(f"  egress ip       : {egress}")
    print(f"  page            : {final_url} (http {code}, {len(html)//1024} KB)")
    print(f"  playability     : {status}" + (f' -- "{reason}"' if reason else ""))
    verdict = {
        "OK": "PLAYS from this network (anonymous, no login).",
        "LOGIN_REQUIRED": "BOT WALL: this network's IP is treated as a bot "
                          "until you sign in.",
        "UNPLAYABLE": "Unplayable for another reason (geo/removed video).",
        "ERROR": "Server-side error verdict.",
        "UNKNOWN": "Could not read a verdict.",
    }.get(status, "See status above.")
    print(f"  verdict         : {verdict}")
    interp = {
        "OK": "IP reputation is NOT blocking anonymous playback.",
        "LOGIN_REQUIRED": "IP reputation IS the blocking factor for anonymous playback.",
    }.get(status, "Inspect the status/reason above.")
    print(f"  interpretation  : {interp}")
    return 0 if status == "OK" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", default=DEFAULT_VIDEO,
                    help="YouTube watch URL to test")
    ap.add_argument("--label", default="this machine")
    ap.add_argument("--via-ssh", metavar="USER@HOST",
                    help="run the test on a farm runner instead of here")
    ap.add_argument("--ssh-port", default="2222")
    a = ap.parse_args()

    if a.via_ssh:
        # ship this very file to the runner and execute it there unchanged
        cmd = "python3 - --label 'runner (%s)' '%s'" % (a.via_ssh, a.video)
        r = subprocess.run(["ssh", "-p", a.ssh_port, "-o", "BatchMode=yes",
                            "-o", "StrictHostKeyChecking=accept-new",
                            a.via_ssh, cmd],
                           input=open(__file__, "rb").read(), timeout=120)
        return r.returncode
    return run_test(a.label, a.video)


if __name__ == "__main__":
    sys.exit(main())
