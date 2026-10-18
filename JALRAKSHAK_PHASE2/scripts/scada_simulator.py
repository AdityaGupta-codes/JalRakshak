"""SCADA / sensor simulator: sends readings to JalRakshak like a field gateway would.

Register the devices first (admin panel > Field data > Add device) and copy their keys, then:

    python scripts/scada_simulator.py --api http://localhost:8000 \\
        --level-key jdk_...            # a reservoir level sensor (litres in the reservoir)
        --flow-key jdk_...             # a zone flow meter (litres since the previous reading); repeatable
        --every 10                     # seconds between readings (default 10)

Uses only the Python standard library. Stop with Ctrl+C. Each run is a demonstration, not real data.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def send(api, key, value):
    body = json.dumps({"readings": [{"value": round(value, 2),
                                     "measured_at": datetime.now(timezone.utc).isoformat()}]}).encode()
    req = urllib.request.Request(api.rstrip("/") + "/telemetry/readings", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Device-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except urllib.error.URLError as e:
        return 0, {"detail": str(e.reason)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--level-key", help="device key of a reservoir level sensor")
    ap.add_argument("--level-start", type=float, default=48000, help="starting level in litres")
    ap.add_argument("--flow-key", action="append", default=[], help="device key of a zone flow meter")
    ap.add_argument("--every", type=float, default=10, help="seconds between readings")
    ap.add_argument("--count", type=int, default=0, help="stop after this many rounds (0 = run until Ctrl+C)")
    a = ap.parse_args(argv)
    if not a.level_key and not a.flow_key:
        ap.error("give at least --level-key or --flow-key")
    level, n = a.level_start, 0
    try:
        while True:
            if a.level_key:
                level = max(0.0, level + random.uniform(-400, 250))   # draw-down with some refilling
                print("level", round(level), *send(a.api, a.level_key, level))
            for k in a.flow_key:
                print("flow ", k[:10] + "...", *send(a.api, k, random.uniform(20000, 60000)))
            n += 1
            if a.count and n >= a.count:
                break
            time.sleep(a.every)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
