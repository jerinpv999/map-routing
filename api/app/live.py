"""
Live ride tracking.

Rider app ── POST /api/orders/{id}/location (every 5–10 s) ──▶ API
                                                         │  stores point, re-routes from the
                                                         │  current position to pickup or drop,
                                                         │  updates ETA, NOTIFY dr_live
Map page ◀── WebSocket /api/orders/{id}/ws ◀─────────────┘

Status flow:  assigned → to_pickup → picked_up → delivered   (or cancelled)
  - before pickup: route goes rider → pickup
  - after pickup:  route goes rider → customer

Postgres LISTEN/NOTIFY fans updates out to every API worker, so no Redis is needed.
"""
import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import db, osrm

log = logging.getLogger("dr-api.live")
router = APIRouter(prefix="/api/orders")

ARRIVE_RADIUS_M = 60          # "arrived" when within this many metres of the target
MAX_ACCURACY_M = 150          # GPS fixes worse than this are stored but don't move the rider
TRAIL_POINTS = 300            # how much of the travelled path the map gets
# pickup / delivery confirmation: rider must be this close to the pickup or customer
COMPLETE_RADIUS_M = float(os.getenv("DR_COMPLETE_RADIUS_M", "250"))
# 1 = refuse to confirm when the rider's position is unknown; 0 = allow it
REQUIRE_LOCATION = os.getenv("DR_COMPLETE_REQUIRE_LOCATION", "0") == "1"
LAST_FIX_MAX_AGE_S = 180       # a stored GPS fix older than this doesn't count as "here now"

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_listener_task: asyncio.Task | None = None


# ------------------------------------------------------------------ models --

class LocationUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    rider_id: Optional[str] = None
    heading: Optional[float] = Field(None, ge=0, le=360, description="degrees, 0 = north")
    speed: Optional[float] = Field(None, ge=0, description="metres per second")
    accuracy: Optional[float] = Field(None, ge=0, description="GPS accuracy in metres")
    recorded_at: Optional[datetime] = Field(None, description="when the phone took the fix")


class StatusUpdate(BaseModel):
    status: Literal["to_pickup", "picked_up", "delivered", "cancelled"]


class Completion(BaseModel):
    rider_id: str = Field(..., examples=["RIDER-7"], description="must be the rider assigned to the order")
    lat: Optional[float] = Field(None, ge=-90, le=90, description="rider's position when tapping the button")
    lng: Optional[float] = Field(None, ge=-180, le=180)
    note: Optional[str] = Field(None, max_length=500, examples=["Handed to security"],
                                description="optional: receiver name, OTP checked, remarks")


# ----------------------------------------------------------- DB helpers --

SCHEMA = """
CREATE TABLE IF NOT EXISTS dr_live_orders (
    order_id        TEXT PRIMARY KEY,
    rider_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'assigned',
    pickup_pt       GEOMETRY(Point, 4326) NOT NULL,
    drop_pt         GEOMETRY(Point, 4326) NOT NULL,
    rider_pt        GEOMETRY(Point, 4326),
    heading         DOUBLE PRECISION,
    speed_mps       DOUBLE PRECISION,
    last_seen       TIMESTAMPTZ,
    remaining_m     DOUBLE PRECISION,
    remaining_s     DOUBLE PRECISION,
    eta             TIMESTAMPTZ,
    remaining_geom  GEOMETRY(LineString, 4326),
    picked_up_at    TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dr_rider_locations (
    id           BIGSERIAL PRIMARY KEY,
    order_id     TEXT NOT NULL,
    rider_id     TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    pt           GEOMETRY(Point, 4326) NOT NULL,
    heading      DOUBLE PRECISION,
    speed_mps    DOUBLE PRECISION,
    accuracy_m   DOUBLE PRECISION,
    status       TEXT
);
CREATE INDEX IF NOT EXISTS dr_rider_locations_order_idx ON dr_rider_locations (order_id, recorded_at);
ALTER TABLE dr_live_orders
    ADD COLUMN IF NOT EXISTS picked_up_by       TEXT,
    ADD COLUMN IF NOT EXISTS picked_up_pt       GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS picked_up_dist_m   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pickup_note        TEXT,
    ADD COLUMN IF NOT EXISTS delivered_by       TEXT,
    ADD COLUMN IF NOT EXISTS delivered_pt       GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS delivered_dist_m   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS delivery_note      TEXT;
CREATE INDEX IF NOT EXISTS dr_live_orders_rider_idx ON dr_live_orders (rider_id, status);
"""


async def ensure_schema():
    """Creates live-tracking tables on startup (works on an existing dr-postgis volume)."""
    async with db.pool().connection() as conn:
        # several API workers start at once; only one may run the DDL at a time
        await conn.execute("SELECT pg_advisory_xact_lock(724100)")
        await conn.execute(SCHEMA)


async def register_order(order_id: str, pickup, drop, rider=None, rider_id=None):
    """Called by /api/order-route so a planned order can be tracked."""
    sql = """
        INSERT INTO dr_live_orders (order_id, rider_id, pickup_pt, drop_pt, rider_pt)
        VALUES (%s, %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                CASE WHEN %s::float8 IS NULL THEN NULL
                     ELSE ST_SetSRID(ST_MakePoint(%s, %s), 4326) END)
        ON CONFLICT (order_id) DO UPDATE SET
            pickup_pt = EXCLUDED.pickup_pt,
            drop_pt   = EXCLUDED.drop_pt,
            rider_pt  = COALESCE(EXCLUDED.rider_pt, dr_live_orders.rider_pt),
            rider_id  = COALESCE(EXCLUDED.rider_id, dr_live_orders.rider_id),
            updated_at = now()
        WHERE dr_live_orders.status IN ('assigned', 'to_pickup')
    """
    rlng = rider.lng if rider else None
    rlat = rider.lat if rider else None
    async with db.pool().connection() as conn:
        await conn.execute(sql, (order_id, rider_id, pickup.lng, pickup.lat, drop.lng, drop.lat,
                                 rlng, rlng, rlat))


async def set_initial_route(order_id: str, rt: dict):
    async with db.pool().connection() as conn:
        await _save_state(conn, order_id, rt=rt)


async def _load(order_id: str) -> dict | None:
    sql = """
        SELECT status, ST_Y(pickup_pt), ST_X(pickup_pt), ST_Y(drop_pt), ST_X(drop_pt),
               ST_Y(rider_pt), ST_X(rider_pt), rider_id,
               EXTRACT(EPOCH FROM now() - last_seen)
        FROM dr_live_orders WHERE order_id = %s
    """
    async with db.pool().connection() as conn:
        r = await (await conn.execute(sql, (order_id,))).fetchone()
    if not r:
        return None
    return {"status": r[0], "pickup": (r[1], r[2]), "drop": (r[3], r[4]),
            "rider": (r[5], r[6]) if r[5] is not None else None,
            "rider_id": r[7], "fix_age_s": float(r[8]) if r[8] is not None else None}


def _target(status: str, o: dict):
    if status in ("assigned", "to_pickup"):
        return "pickup", o["pickup"]
    if status == "picked_up":
        return "drop", o["drop"]
    return None, None


async def _reroute(rider: tuple, target: tuple | None):
    """Remaining route from the rider to the target. None if no target or no route."""
    if not target:
        return None
    try:
        return await osrm.route([rider, target])
    except HTTPException as e:
        log.warning("reroute failed: %s", e.detail)
        return None


async def _save_state(conn, order_id: str, *, status=None, rider=None, rt=None, clear_route=False,
                      heading=None, speed=None, seen_at=None, rider_id=None):
    now = datetime.now(timezone.utc)
    sets, args = ["updated_at = now()"], []
    if status:
        sets.append("status = %s"); args.append(status)
        if status == "picked_up":
            sets.append("picked_up_at = COALESCE(picked_up_at, now())")
        if status == "delivered":
            sets.append("delivered_at = COALESCE(delivered_at, now())")
    if rider:
        sets.append("rider_pt = ST_SetSRID(ST_MakePoint(%s, %s), 4326)"); args += [rider[1], rider[0]]
        sets.append("heading = %s"); args.append(heading)
        sets.append("speed_mps = %s"); args.append(speed)
        sets.append("last_seen = %s"); args.append(seen_at or now)
    if rider_id:
        sets.append("rider_id = %s"); args.append(rider_id)
    if rt:
        sets.append("remaining_m = %s"); args.append(rt["distance_m"])
        sets.append("remaining_s = %s"); args.append(rt["duration_s"])
        sets.append("eta = %s"); args.append(now + timedelta(seconds=rt["duration_s"]))
        sets.append("remaining_geom = ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326)")
        args.append(json.dumps(rt["geometry"]))
    elif clear_route:
        sets += ["remaining_m = NULL", "remaining_s = NULL", "eta = NULL", "remaining_geom = NULL"]
    args.append(order_id)
    await conn.execute(f"UPDATE dr_live_orders SET {', '.join(sets)} WHERE order_id = %s", args)
    # Tell every API worker (and so every open map) that this order changed.
    await conn.execute("SELECT pg_notify('dr_live', %s)", (order_id,))


async def get_state(order_id: str, trail: bool = True) -> dict | None:
    sql = """
        SELECT order_id, rider_id, status,
               ST_Y(pickup_pt), ST_X(pickup_pt), ST_Y(drop_pt), ST_X(drop_pt),
               ST_Y(rider_pt), ST_X(rider_pt), heading, speed_mps, last_seen,
               remaining_m, remaining_s, eta, ST_AsGeoJSON(remaining_geom)::json,
               picked_up_at, delivered_at, updated_at,
               picked_up_by, delivered_by, pickup_note, delivery_note,
               ST_Distance(rider_pt::geography,
                   CASE WHEN status = 'picked_up' THEN drop_pt ELSE pickup_pt END::geography)
        FROM dr_live_orders WHERE order_id = %s
    """
    async with db.pool().connection() as conn:
        r = await (await conn.execute(sql, (order_id,))).fetchone()
        if not r:
            return None
        trail_coords = []
        if trail:
            rows = await (await conn.execute(
                """SELECT ST_X(pt), ST_Y(pt) FROM (
                       SELECT pt, recorded_at FROM dr_rider_locations
                       WHERE order_id = %s AND (accuracy_m IS NULL OR accuracy_m <= %s)
                       ORDER BY recorded_at DESC LIMIT %s) t
                   ORDER BY recorded_at""",
                (order_id, MAX_ACCURACY_M, TRAIL_POINTS))).fetchall()
            trail_coords = [[x, y] for x, y in rows]

    iso = lambda d: d.isoformat() if d else None
    status = r[2]
    target_name = "pickup" if status in ("assigned", "to_pickup") else "drop" if status == "picked_up" else None
    dist_to_target = r[23]
    features = []
    if len(trail_coords) >= 2:
        features.append({"type": "Feature", "properties": {"kind": "trail"},
                         "geometry": {"type": "LineString", "coordinates": trail_coords}})
    if r[15]:
        features.append({"type": "Feature", "properties": {"kind": "remaining", "target": target_name},
                         "geometry": r[15]})
    return {
        "order_id": r[0], "rider_id": r[1], "status": status, "target": target_name,
        "pickup": {"lat": r[3], "lng": r[4]}, "drop": {"lat": r[5], "lng": r[6]},
        "rider": {"lat": r[7], "lng": r[8], "heading": r[9], "speed_mps": r[10],
                  "last_seen": iso(r[11])} if r[7] is not None else None,
        "remaining_m": r[12], "remaining_s": r[13], "eta": iso(r[14]),
        "arrived": dist_to_target is not None and target_name is not None
                   and dist_to_target <= ARRIVE_RADIUS_M,
        "distance_to_target_m": round(dist_to_target, 1) if dist_to_target is not None else None,
        "picked_up_at": iso(r[16]), "delivered_at": iso(r[17]), "updated_at": iso(r[18]),
        "picked_up_by": r[19], "delivered_by": r[20], "pickup_note": r[21], "delivery_note": r[22],
        "geojson": {"type": "FeatureCollection", "features": features},
    }


def _need_db():
    if not db.is_connected():
        raise HTTPException(503, "Live tracking needs the database (DR_SAVE_ROUTES=1)")


# ---------------------------------------------------------------- routes --

@router.post("/{order_id}/location")
async def update_location(order_id: str, u: LocationUpdate):
    """Rider app calls this every 5–10 seconds during the ride."""
    _need_db()
    o = await _load(order_id)
    if not o:
        raise HTTPException(404, "Unknown order. Create it first with POST /api/order-route and an order_id")
    if o["status"] in ("delivered", "cancelled"):
        raise HTTPException(409, f"Order is already {o['status']}")

    status = "to_pickup" if o["status"] == "assigned" else o["status"]
    recorded = u.recorded_at or datetime.now(timezone.utc)
    good_fix = u.accuracy is None or u.accuracy <= MAX_ACCURACY_M

    async with db.pool().connection() as conn:
        await conn.execute(
            """INSERT INTO dr_rider_locations
                 (order_id, rider_id, recorded_at, pt, heading, speed_mps, accuracy_m, status)
               VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s)""",
            (order_id, u.rider_id, recorded, u.lng, u.lat, u.heading, u.speed, u.accuracy, status))

    if good_fix:
        _, target = _target(status, o)
        rt = await _reroute((u.lat, u.lng), target)
        async with db.pool().connection() as conn:
            await _save_state(conn, order_id, status=status, rider=(u.lat, u.lng), rt=rt,
                              heading=u.heading, speed=u.speed, seen_at=recorded, rider_id=u.rider_id)

    s = await get_state(order_id, trail=False)
    return {k: s[k] for k in ("order_id", "status", "target", "remaining_m", "remaining_s",
                              "eta", "arrived", "distance_to_target_m")} | {"used_fix": good_fix}


@router.post("/{order_id}/status")
async def update_status(order_id: str, u: StatusUpdate):
    """Ops / admin override: sets any status with no rider or location checks.
    Riders should use /pickup and /deliver instead."""
    _need_db()
    o = await _load(order_id)
    if not o:
        raise HTTPException(404, "Unknown order")
    if o["status"] in ("delivered", "cancelled"):
        raise HTTPException(409, f"Order is already {o['status']}")

    _, target = _target(u.status, o)
    rt = await _reroute(o["rider"], target) if (o["rider"] and target) else None
    async with db.pool().connection() as conn:
        await _save_state(conn, order_id, status=u.status, rt=rt, clear_route=not rt)
    return await get_state(order_id, trail=False)


def _dist_m(a: tuple, b: tuple) -> float:
    """Great-circle distance in metres between (lat, lng) points."""
    import math
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


async def _complete(order_id: str, c: Completion, step: str):
    """Shared logic for pickup and delivery confirmation."""
    _need_db()
    o = await _load(order_id)
    if not o:
        raise HTTPException(404, "Unknown order")

    # 1. right rider?  (an order created without rider_id is claimed by the first rider to confirm)
    if o["rider_id"] and o["rider_id"] != c.rider_id:
        raise HTTPException(403, f"Order {order_id} is assigned to a different rider")

    # 2. right moment?
    done_status = "picked_up" if step == "pickup" else "delivered"
    if o["status"] == "cancelled":
        raise HTTPException(409, "Order is cancelled")
    if o["status"] == done_status or (step == "pickup" and o["status"] == "delivered"):
        s = await get_state(order_id, trail=False)          # repeat tap: answer OK, change nothing
        return {"ok": True, "already_done": True, "order": s}
    if step == "delivery" and o["status"] != "picked_up":
        raise HTTPException(409, "Order has not been picked up yet. Confirm pickup first")

    # 3. right place?
    target = o["pickup"] if step == "pickup" else o["drop"]
    if c.lat is not None and c.lng is not None:
        where, source = (c.lat, c.lng), "sent with request"
    elif o["rider"] and o["fix_age_s"] is not None and o["fix_age_s"] <= LAST_FIX_MAX_AGE_S:
        where, source = o["rider"], "last GPS update"
    else:
        where, source = None, None
    dist = _dist_m(where, target) if where else None
    if dist is None and REQUIRE_LOCATION:
        raise HTTPException(422, "Rider location unknown. Send lat and lng with the request")
    if dist is not None and dist > COMPLETE_RADIUS_M:
        raise HTTPException(422, f"Rider is {dist:.0f} m from the {'pickup' if step == 'pickup' else 'customer'} "
                                 f"(limit {COMPLETE_RADIUS_M:.0f} m, position from {source})")

    # 4. save + push to the map
    nxt = "drop" if step == "pickup" else None
    rt = await _reroute(where or o["rider"], o["drop"]) if (nxt and (where or o["rider"])) else None
    col = "picked_up" if step == "pickup" else "delivered"
    note_col = "pickup_note" if step == "pickup" else "delivery_note"
    async with db.pool().connection() as conn:
        await conn.execute(
            f"""UPDATE dr_live_orders SET
                    rider_id = COALESCE(rider_id, %s),
                    {col}_by = %s,
                    {col}_pt = CASE WHEN %s::float8 IS NULL THEN NULL
                                    ELSE ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                    {col}_dist_m = %s,
                    {note_col} = %s
                WHERE order_id = %s""",
            (c.rider_id, c.rider_id,
             where[1] if where else None, where[1] if where else None, where[0] if where else None,
             round(dist, 1) if dist is not None else None, c.note, order_id))
        rider_now = (c.lat, c.lng) if c.lat is not None and c.lng is not None else None
        await _save_state(conn, order_id, status=done_status, rt=rt, clear_route=not rt, rider=rider_now)

    s = await get_state(order_id, trail=False)
    return {"ok": True, "already_done": False,
            "distance_from_target_m": round(dist, 1) if dist is not None else None,
            "location_checked": dist is not None, "order": s}


@router.post("/{order_id}/pickup")
async def confirm_pickup(order_id: str, c: Completion):
    """Rider confirms the parcel is collected. Checks rider ID, order state and distance to pickup."""
    return await _complete(order_id, c, "pickup")


@router.post("/{order_id}/deliver")
async def confirm_delivery(order_id: str, c: Completion):
    """Rider confirms hand-over to the customer. Checks rider ID, that pickup happened, and distance."""
    return await _complete(order_id, c, "delivery")


rider_router = APIRouter(prefix="/api/riders")


@rider_router.get("/{rider_id}/orders")
async def rider_orders(rider_id: str, active: bool = True):
    """Orders for a rider (active only by default), newest first: what the rider app should show."""
    _need_db()
    where = "AND status NOT IN ('delivered', 'cancelled')" if active else ""
    async with db.pool().connection() as conn:
        rows = await (await conn.execute(
            f"""SELECT order_id, status, ST_Y(pickup_pt), ST_X(pickup_pt), ST_Y(drop_pt), ST_X(drop_pt),
                       remaining_m, remaining_s, eta, picked_up_at, delivered_at, created_at
                FROM dr_live_orders WHERE rider_id = %s {where}
                ORDER BY created_at DESC LIMIT 100""", (rider_id,))).fetchall()
    iso = lambda d: d.isoformat() if d else None
    return [{"order_id": r[0], "status": r[1],
             "pickup": {"lat": r[2], "lng": r[3]}, "drop": {"lat": r[4], "lng": r[5]},
             "remaining_m": r[6], "remaining_s": r[7], "eta": iso(r[8]),
             "picked_up_at": iso(r[9]), "delivered_at": iso(r[10]), "created_at": iso(r[11])} for r in rows]


@router.get("/{order_id}/live")
async def live_state(order_id: str):
    """Current position, remaining route, ETA and travelled trail."""
    _need_db()
    s = await get_state(order_id)
    if not s:
        raise HTTPException(404, "Unknown order")
    return s


@router.websocket("/{order_id}/ws")
async def live_ws(ws: WebSocket, order_id: str):
    """Pushes the live state whenever the order changes."""
    await ws.accept()
    if not db.is_connected():
        await ws.close(code=1011, reason="database unavailable")
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _subscribers[order_id].add(q)
    try:
        s = await get_state(order_id)
        await ws.send_json(s or {"order_id": order_id, "error": "unknown order"})
        while True:
            # wake on a change, or every 25 s to keep proxies from closing the socket
            try:
                await asyncio.wait_for(q.get(), timeout=25)
                while not q.empty():          # collapse bursts into one send
                    q.get_nowait()
                s = await get_state(order_id)
                await ws.send_json(s)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _subscribers[order_id].discard(q)
        if not _subscribers[order_id]:
            _subscribers.pop(order_id, None)


# ------------------------------------------------- LISTEN/NOTIFY fan-out --

async def _listen_forever():
    import psycopg
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(db.DSN, autocommit=True) as conn:
                await conn.execute("LISTEN dr_live")
                log.info("listening for live updates")
                async for n in conn.notifies():
                    for q in list(_subscribers.get(n.payload, ())):
                        if not q.full():
                            q.put_nowait(1)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("live listener lost (%s); reconnecting in 3 s", e)
            await asyncio.sleep(3)


async def start():
    global _listener_task
    if db.is_connected():
        await ensure_schema()
        _listener_task = asyncio.create_task(_listen_forever())


async def stop():
    if _listener_task:
        _listener_task.cancel()
