"""PostGIS storage for order routes (dr-postgis, never Airflow's database)."""
import json
import logging
import os

log = logging.getLogger("dr-api.db")
DSN = os.getenv("DR_DB_DSN")
_pool = None


async def connect():
    global _pool
    if not DSN:
        log.warning("DR_DB_DSN not set; routes will not be saved")
        return
    from psycopg_pool import AsyncConnectionPool
    try:
        _pool = AsyncConnectionPool(DSN, min_size=1, max_size=10, open=False)
        await _pool.open(wait=True, timeout=20)
        log.info("connected to routing database")
    except Exception as e:
        log.error("database unavailable, continuing without saving: %s", e)
        _pool = None


async def close():
    if _pool:
        await _pool.close()


def is_connected() -> bool:
    return _pool is not None


def pool():
    return _pool


def _pt(p):
    return None if p is None else f"SRID=4326;POINT({p.lng} {p.lat})"


async def save_order_route(req, to_pickup, to_drop) -> int:
    sql = """
        INSERT INTO dr_order_routes
          (order_id, rider_pt, pickup_pt, drop_pt,
           to_pickup_m, to_pickup_s, to_drop_m, to_drop_s,
           to_pickup_geom, to_drop_geom)
        VALUES
          (%s, ST_GeomFromEWKT(%s::text), ST_GeomFromEWKT(%s::text), ST_GeomFromEWKT(%s::text),
           %s, %s, %s, %s,
           ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326), ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326))
        RETURNING id
    """
    args = (
        req.order_id, _pt(req.rider), _pt(req.pickup), _pt(req.drop),
        to_pickup["distance_m"] if to_pickup else None,
        to_pickup["duration_s"] if to_pickup else None,
        to_drop["distance_m"], to_drop["duration_s"],
        json.dumps(to_pickup["geometry"]) if to_pickup else None,
        json.dumps(to_drop["geometry"]),
    )
    async with _pool.connection() as conn:
        cur = await conn.execute(sql, args)
        return (await cur.fetchone())[0]


async def get_order_route(order_id: str):
    sql = """
        SELECT id, order_id, created_at,
               to_pickup_m, to_pickup_s, to_drop_m, to_drop_s,
               ST_AsGeoJSON(rider_pt)::json, ST_AsGeoJSON(pickup_pt)::json,
               ST_AsGeoJSON(drop_pt)::json,
               ST_AsGeoJSON(to_pickup_geom)::json, ST_AsGeoJSON(to_drop_geom)::json
        FROM dr_order_routes WHERE order_id = %s
        ORDER BY created_at DESC LIMIT 1
    """
    async with _pool.connection() as conn:
        cur = await conn.execute(sql, (order_id,))
        r = await cur.fetchone()
    if not r:
        return None
    feats = []
    if r[10]:
        feats.append({"type": "Feature", "properties": {"leg": "to_pickup"}, "geometry": r[10]})
    feats.append({"type": "Feature", "properties": {"leg": "to_drop"}, "geometry": r[11]})
    return {
        "id": r[0], "order_id": r[1], "created_at": r[2].isoformat(),
        "to_pickup": {"distance_m": r[3], "duration_s": r[4]} if r[3] is not None else None,
        "to_drop": {"distance_m": r[5], "duration_s": r[6]},
        "points": {"rider": r[7], "pickup": r[8], "drop": r[9]},
        "geojson": {"type": "FeatureCollection", "features": feats},
    }
