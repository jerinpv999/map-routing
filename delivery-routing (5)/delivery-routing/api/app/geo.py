"""
Address search (typeahead suggestions) and reverse lookup (what is at this pin),
served from the dr_geo schema that ./scripts/setup.sh builds from the .osm.pbf files.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg import errors as pgerr

from . import db

log = logging.getLogger("dr-api.geo")
router = APIRouter(prefix="/api/geocode", tags=["Address search"])

KIND_LABEL = {"place": "Area", "street": "Street", "poi": "Place", "address": "Address"}

SEARCH_SQL = """
WITH q AS (SELECT public.dr_norm(%(q)s) AS t),
w AS (SELECT coalesce(array_agg(x), '{}') AS words
      FROM unnest(string_to_array((SELECT t FROM q), ' ')) x WHERE length(x) >= 2),
cand AS (                       -- best text matches first (uses the trigram index)
  SELECT f.*, word_similarity(q.t, f.search) AS sim
  FROM dr_geo.features f, q
  WHERE q.t <%% f.search
  ORDER BY sim DESC, f.importance DESC
  LIMIT 300
),
ranked AS (                     -- drop weak matches when strong ones exist ("mg road" vs any "road")
  SELECT cand.*, max(sim) OVER () AS best FROM cand
)
SELECT id, kind, category, name, housenumber, housename, road, locality, city, district, state, postcode,
       ST_Y(geom) AS lat, ST_X(geom) AS lng, sim,
       sim * 60
       + importance * 0.6
       -- share of the typed words that start a word in this result ("mall kochi": the mall has both)
       + 40 * (SELECT count(*) FROM unnest((SELECT words FROM w)) x WHERE search ~ ('(^| )' || x))::float
            / greatest(1, cardinality((SELECT words FROM w)))
       + CASE WHEN public.dr_norm(name) = (SELECT t FROM q) THEN 25
              WHEN public.dr_norm(name) LIKE (SELECT t FROM q) || '%%' THEN 15
              WHEN public.dr_norm(name) LIKE '%% ' || (SELECT t FROM q) || '%%' THEN 6
              ELSE 0 END
       + CASE WHEN %(lat)s::float8 IS NULL THEN 0
              ELSE 25 * exp(-ST_Distance(geom::geography,
                                         ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography) / 15000.0)
         END AS score
FROM ranked
WHERE sim >= best - 0.25
  AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s::text[]))
ORDER BY score DESC
LIMIT %(limit)s
"""

REVERSE_SQL = """
WITH p AS (SELECT ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326) AS g)
SELECT poi.name AS poi_name, poi.category AS poi_category, poi.d AS poi_d,
       adr.housenumber, adr.housename, adr.road AS adr_road,
       rd.name AS road, c.locality, c.city, c.district, c.state, pc.postcode
FROM p
CROSS JOIN LATERAL dr_geo.ctx(p.g) c
LEFT JOIN LATERAL (
    SELECT x.* FROM (SELECT f.name, f.category, ST_Distance(f.geom::geography, p.g::geography) AS d
                     FROM dr_geo.features f WHERE f.kind = 'poi' ORDER BY f.geom <-> p.g LIMIT 5) x
    WHERE x.d <= %(poi_m)s ORDER BY x.d LIMIT 1) poi ON true
LEFT JOIN LATERAL (
    SELECT x.* FROM (SELECT f.housenumber, f.housename, f.road, ST_Distance(f.geom::geography, p.g::geography) AS d
                     FROM dr_geo.features f WHERE f.kind = 'address' ORDER BY f.geom <-> p.g LIMIT 3) x
    WHERE x.d <= 30 ORDER BY x.d LIMIT 1) adr ON true
LEFT JOIN LATERAL (
    SELECT x.* FROM (SELECT s.name, ST_Distance(s.geom::geography, p.g::geography) AS d
                     FROM dr_geo.street_lines s ORDER BY s.geom <-> p.g LIMIT 5) x
    WHERE x.d <= %(road_m)s ORDER BY x.d LIMIT 1) rd ON true
LEFT JOIN LATERAL (
    SELECT x.postcode FROM (SELECT f.postcode, f.geom FROM dr_geo.features f WHERE f.postcode IS NOT NULL
                            ORDER BY f.geom <-> p.g LIMIT 1) x
    WHERE ST_DWithin(x.geom::geography, p.g::geography, 3000)) pc ON true
"""


def _dedupe(parts):
    out, seen = [], set()
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower()); out.append(p)
    return out


def _subtitle(r: dict) -> str:
    parts = []
    if r["kind"] in ("poi", "address") and r["road"] and r["road"].lower() not in r["name"].lower():
        parts.append(r["road"])
    parts += [r["locality"], r["city"], r["district"], r["state"]]
    return ", ".join(_dedupe([p for p in parts if p and p != r["name"]]))


async def _query(sql: str, params: dict):
    if not db.is_connected():
        raise HTTPException(503, "Address search needs the database")
    try:
        async with db.pool().connection() as conn:
            await conn.execute("SET pg_trgm.word_similarity_threshold = 0.4")
            cur = await conn.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in await cur.fetchall()]
    except (pgerr.UndefinedTable, pgerr.InvalidSchemaName, pgerr.UndefinedFunction):
        raise HTTPException(503, "Address search data is not built yet. Run ./scripts/setup.sh")


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="What the customer typed", examples=["lulu mall"]),
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Prefer results near this point"),
    lng: Optional[float] = Query(None, ge=-180, le=180),
    limit: int = Query(8, ge=1, le=20),
    kinds: Optional[str] = Query(None, description="Comma list of place,street,poi,address", examples=["place,poi"]),
):
    """Typeahead suggestions for an address or landmark. Typo-tolerant; results near lat/lng rank higher."""
    if len(q.strip()) < 2:
        return []
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    if (lat is None) != (lng is None):
        lat = lng = None
    rows = await _query(SEARCH_SQL, {"q": q, "lat": lat, "lng": lng, "limit": limit, "kinds": kind_list})
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "kind": r["kind"],
            "type": KIND_LABEL.get(r["kind"], r["kind"]),
            "category": r["category"],
            "name": r["name"],
            "subtitle": _subtitle(r),
            "lat": round(r["lat"], 6), "lng": round(r["lng"], 6),
            "zoom": 14 if (r["kind"] == "place" and r["category"] in ("city", "town")) else 16 if r["kind"] in ("place", "street") else 18,
            "address": {
                "house_number": r["housenumber"], "house_name": r["housename"],
                "road": r["road"] if r["kind"] != "street" else r["name"],
                "locality": r["locality"] if r["kind"] != "place" else (r["name"] if r["category"] not in ("city", "town") else None),
                "city": r["city"] if not (r["kind"] == "place" and r["category"] in ("city", "town")) else r["name"],
                "district": r["district"], "state": r["state"], "postcode": r["postcode"],
            },
        })
    return out


@router.get("/reverse")
async def reverse(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    """What is at a pin: nearby landmark, road, locality, city, district, state, postcode."""
    rows = await _query(REVERSE_SQL, {"lat": lat, "lng": lng, "poi_m": 100, "road_m": 80})
    r = rows[0]
    landmark = ({"name": r["poi_name"], "category": r["poi_category"], "distance_m": round(r["poi_d"], 1)}
                if r["poi_name"] else None)
    road_name = r["road"] or r["adr_road"]
    address = {
        "house_number": r["housenumber"], "house_name": r["housename"], "road": road_name,
        "locality": r["locality"], "city": r["city"], "district": r["district"], "state": r["state"],
        "postcode": r["postcode"],
    }
    house = " ".join(x for x in (r["housename"], r["housenumber"]) if x) or None
    head = house or (f"Near {landmark['name']}" if landmark else None)
    display = ", ".join(_dedupe([head, road_name, r["locality"], r["city"]])) \
        or ", ".join(_dedupe([r["district"], r["state"]]))
    return {"display": display or None, "landmark": landmark, "address": address}


LABELS_SQL = """
SELECT kind, category, name, housenumber, housename, ST_X(geom) AS lng, ST_Y(geom) AS lat
FROM dr_geo.features
WHERE geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
  AND (kind = 'address' OR (kind = 'poi' AND (category LIKE 'building=%%' OR category LIKE 'landuse=%%')))
ORDER BY importance DESC
LIMIT 1500
"""


@router.get("/labels")
async def labels(bbox: str = Query(..., description="minLng,minLat,maxLng,maxLat of the map view",
                                   examples=["76.305,10.024,76.315,10.030"])):
    """House names / numbers and named buildings in a small map area, as GeoJSON points (for map labels).
    Shops and landmarks are already labelled from the map tiles. Areas larger than ~3 x 3 km return nothing."""
    try:
        w, s_, e, n = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise HTTPException(422, "bbox must be minLng,minLat,maxLng,maxLat")
    if not (w < e and s_ < n) or (e - w) * (n - s_) > 0.0009:
        return {"type": "FeatureCollection", "features": []}
    rows = await _query(LABELS_SQL, {"w": w, "s": s_, "e": e, "n": n})
    feats = []
    for r in rows:
        if r["kind"] == "address":
            label = " ".join(x for x in (r["housename"], r["housenumber"]) if x) or r["name"]
        else:
            label = r["name"]
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [round(r["lng"], 6), round(r["lat"], 6)]},
                      "properties": {"label": label, "kind": "house" if r["kind"] == "address" else "building"}})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/stats")
async def stats():
    """How much address search data is loaded, by kind (to check what your .osm.pbf files contain)."""
    rows = await _query("""SELECT kind, count(*) AS n,
                                  count(*) FILTER (WHERE housename IS NOT NULL) AS with_house_name,
                                  count(*) FILTER (WHERE housenumber IS NOT NULL) AS with_house_number,
                                  count(*) FILTER (WHERE category LIKE 'building=%%') AS named_buildings
                           FROM dr_geo.features GROUP BY kind ORDER BY kind""", {})
    by = {r["kind"]: r for r in rows}
    a, p = by.get("address", {}), by.get("poi", {})
    return {
        "areas": by.get("place", {}).get("n", 0),
        "streets": by.get("street", {}).get("n", 0),
        "landmarks": p.get("n", 0),
        "named_buildings": p.get("named_buildings", 0),
        "addresses": a.get("n", 0),
        "addresses_with_house_name": a.get("with_house_name", 0),
        "addresses_with_house_number": a.get("with_house_number", 0),
    }
