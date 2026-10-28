#!/usr/bin/env python3
"""
Fake a rider driving an order so you can watch the live map move.

    python3 scripts/simulate_ride.py ORD-1001                  # uses http://localhost:8001
    python3 scripts/simulate_ride.py ORD-1001 --api http://SERVER:8088 --every 3

The order must exist (POST /api/order-route with order_id, ideally with a rider point).
Drives rider → pickup along the real road route, marks "picked_up", drives to the
customer, then marks "delivered". Needs only the Python standard library.
"""
import argparse
import json
import math
import time
import urllib.request


def call(api, method, path, body=None):
    req = urllib.request.Request(f"{api}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def bearing(a, b):
    (lng1, lat1), (lng2, lat2) = map(lambda p: (math.radians(p[0]), math.radians(p[1])), (a, b))
    y = math.sin(lng2 - lng1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lng2 - lng1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def drive(api, order, coords, every, steps):
    stride = max(1, len(coords) // steps)
    path = coords[::stride] + [coords[-1]]
    for i, p in enumerate(path):
        nxt = path[min(i + 1, len(path) - 1)]
        d = call(api, "POST", f"/api/orders/{order}/location",
                 {"lat": p[1], "lng": p[0], "heading": round(bearing(p, nxt)), "speed": 8.0, "accuracy": 8})
        km = (d["remaining_m"] or 0) / 1000
        print(f"  {d['status']:<10} → {d['target']:<6} {km:6.2f} km left  arrived={d['arrived']}")
        time.sleep(every)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("order")
    ap.add_argument("--api", default="http://localhost:8001")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between updates")
    ap.add_argument("--steps", type=int, default=25, help="updates per leg")
    a = ap.parse_args()

    s = call(a.api, "GET", f"/api/orders/{a.order}/live")
    if not s["rider"]:
        raise SystemExit("Order has no rider position yet. Create it with a 'rider' point or send one location first.")
    leg = lambda frm, to: call(a.api, "POST", "/api/route",
                               {"points": [frm, to]})["geojson"]["features"][0]["geometry"]["coordinates"]

    print("Leg 1: rider → pickup")
    drive(a.api, a.order, leg(s["rider"], s["pickup"]), a.every, a.steps)
    print(call(a.api, "POST", f"/api/orders/{a.order}/status", {"status": "picked_up"})["status"])
    print("Leg 2: pickup → customer")
    drive(a.api, a.order, leg(s["pickup"], s["drop"]), a.every, a.steps)
    print(call(a.api, "POST", f"/api/orders/{a.order}/status", {"status": "delivered"})["status"])


if __name__ == "__main__":
    main()
