"""
Delivery routing API.

Planning an instant order:
    rider (optional) ──leg 1──▶ pickup ──leg 2──▶ customer
Live tracking during the ride: see live.py

OSRM does the road routing; this service validates input, calls OSRM, returns
GeoJSON for the map, and stores routes / live positions in PostGIS.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db, geo, live, osrm

log = logging.getLogger("dr-api")
logging.basicConfig(level=logging.INFO)

SAVE_ROUTES = os.getenv("DR_SAVE_ROUTES", "1") == "1"


def _load_bbox() -> list[float]:
    """Area covered by the loaded regions (written by setup.sh), with a small margin."""
    path = os.getenv("DR_BBOX_FILE")
    if path and os.path.exists(path):
        try:
            b = [float(v) for v in open(path).read().strip().split(",")]
            if len(b) == 4:
                log.info("map area from %s: %s", path, b)
                return [b[0] - 0.05, b[1] - 0.05, b[2] + 0.05, b[3] + 0.05]
        except ValueError:
            log.warning("could not read %s, using DR_BBOX", path)
    return [float(v) for v in os.getenv("DR_BBOX", "68.0,6.0,97.5,37.5").split(",")]


MIN_LNG, MIN_LAT, MAX_LNG, MAX_LAT = _load_bbox()


# ------------------------------------------------------------------ models --

class Point(BaseModel):
    lat: float = Field(..., ge=-90, le=90, examples=[9.9312])
    lng: float = Field(..., ge=-180, le=180, examples=[76.2673])

    def t(self) -> tuple[float, float]:
        return (self.lat, self.lng)


class OrderRouteRequest(BaseModel):
    order_id: Optional[str] = Field(None, examples=["ORD-1001"],
                                    description="Give one to enable live tracking for this order")
    rider_id: Optional[str] = Field(None, examples=["RIDER-7"])
    rider: Optional[Point] = Field(None, description="Rider's current location (optional)")
    pickup: Point
    drop: Point = Field(..., description="Customer location")


class RouteRequest(BaseModel):
    points: list[Point] = Field(..., min_length=2, max_length=25)


# -------------------------------------------------------------------- app --

@asynccontextmanager
async def lifespan(app: FastAPI):
    await osrm.start()
    if SAVE_ROUTES:
        await db.connect()
        await live.start()
    yield
    await live.stop()
    await osrm.stop()
    await db.close()


app = FastAPI(title="Delivery Routing API", version="1.1", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
_origins = [o.strip() for o in os.getenv("DR_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"])
app.include_router(live.router)
app.include_router(live.rider_router)
app.include_router(geo.router)


def _check_bbox(name: str, p: Point):
    if not (MIN_LNG <= p.lng <= MAX_LNG and MIN_LAT <= p.lat <= MAX_LAT):
        raise HTTPException(422, f"{name} ({p.lat}, {p.lng}) is outside the map area. "
                                 "Check that lat and lng are not swapped.")


def _feature(leg: dict, name: str) -> dict:
    return {"type": "Feature",
            "properties": {"leg": name, "distance_m": leg["distance_m"],
                           "duration_s": leg["duration_s"]},
            "geometry": leg["geometry"]}


# ---------------------------------------------------------------- routes --

@app.get("/api/health")
async def health():
    return {"status": "ok" if await osrm.healthy() else "degraded",
            "osrm": await osrm.healthy(), "db": db.is_connected()}


@app.post("/api/order-route")
async def order_route(req: OrderRouteRequest):
    """Rider → pickup (if rider given) and pickup → customer."""
    if req.rider:
        _check_bbox("rider", req.rider)
    _check_bbox("pickup", req.pickup)
    _check_bbox("drop", req.drop)

    jobs = [osrm.route([req.pickup.t(), req.drop.t()])]
    if req.rider:
        jobs.insert(0, osrm.route([req.rider.t(), req.pickup.t()]))
    results = await asyncio.gather(*jobs)
    to_pickup = results[0] if req.rider else None
    to_drop = results[-1]

    features = []
    if to_pickup:
        features.append(_feature(to_pickup, "to_pickup"))
    features.append(_feature(to_drop, "to_drop"))

    total_m = to_drop["distance_m"] + (to_pickup["distance_m"] if to_pickup else 0)
    total_s = to_drop["duration_s"] + (to_pickup["duration_s"] if to_pickup else 0)

    out = {
        "order_id": req.order_id,
        "to_pickup": {k: to_pickup[k] for k in ("distance_m", "duration_s")} if to_pickup else None,
        "to_drop": {k: to_drop[k] for k in ("distance_m", "duration_s")},
        "total": {"distance_m": round(total_m, 1), "duration_s": round(total_s, 1)},
        "snapped": {
            "rider": to_pickup["snapped"][0] if to_pickup else None,
            "pickup": to_drop["snapped"][0],
            "drop": to_drop["snapped"][1],
        },
        "geojson": {"type": "FeatureCollection", "features": features},
        "saved_id": None,
        "live_tracking": False,
    }

    if SAVE_ROUTES and db.is_connected():
        try:
            out["saved_id"] = await db.save_order_route(req, to_pickup, to_drop)
            if req.order_id:
                await live.register_order(req.order_id, req.pickup, req.drop, req.rider, req.rider_id)
                if to_pickup:          # show the ETA to pickup on the live map straight away
                    await live.set_initial_route(req.order_id, to_pickup)
                out["live_tracking"] = True
        except Exception as e:                       # never fail the route on a DB hiccup
            log.exception("could not save route: %s", e)
    return out


@app.get("/api/nearest")
async def nearest(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    """Nearest road to a point, e.g. to show 'near <road>' while a customer places their pin."""
    in_area = MIN_LNG <= lng <= MAX_LNG and MIN_LAT <= lat <= MAX_LAT
    if not in_area:
        return {"in_service_area": False, "road": None}
    w = await osrm.nearest(lat, lng)
    return {"in_service_area": True,
            "road": None if not w else {"name": w["road_name"], "distance_m": w["distance_m"],
                                        "lat": w["location"][1], "lng": w["location"][0]}}


@app.get("/api/area")
async def area():
    """Bounding box of the loaded map regions [minLng, minLat, maxLng, maxLat]."""
    return {"bbox": [MIN_LNG, MIN_LAT, MAX_LNG, MAX_LAT]}


@app.post("/api/route")
async def route(req: RouteRequest):
    """Generic route through 2–25 points in the given order."""
    for i, p in enumerate(req.points):
        _check_bbox(f"point {i}", p)
    leg = await osrm.route([p.t() for p in req.points])
    return {"distance_m": leg["distance_m"], "duration_s": leg["duration_s"],
            "snapped": leg["snapped"],
            "geojson": {"type": "FeatureCollection", "features": [_feature(leg, "route")]}}


@app.get("/api/order-route/{order_id}")
async def get_order_route(order_id: str):
    """Latest saved planned route for an order."""
    if not db.is_connected():
        raise HTTPException(503, "Database not connected")
    row = await db.get_order_route(order_id)
    if not row:
        raise HTTPException(404, "No saved route for this order")
    return row
