"""Thin async client for the OSRM routing engine."""
import logging
import os

import httpx
from fastapi import HTTPException

log = logging.getLogger("dr-api.osrm")

OSRM_URL = os.getenv("DR_OSRM_URL", "http://dr-osrm:5000").rstrip("/")
OSRM_PROFILE = os.getenv("DR_OSRM_PROFILE_PATH", "driving")

client: httpx.AsyncClient | None = None


async def start():
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))


async def stop():
    if client:
        await client.aclose()


async def route(points: list[tuple[float, float]]) -> dict:
    """points: [(lat, lng), ...] in travel order."""
    coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    url = f"{OSRM_URL}/route/v1/{OSRM_PROFILE}/{coords}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    try:
        r = await client.get(url, params=params)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.error("OSRM unreachable: %s", e)
        raise HTTPException(503, "Routing engine is not reachable")
    if data.get("code") != "Ok":
        msg = data.get("message") or data.get("code")
        raise HTTPException(422, f"No route found: {msg}")
    rt = data["routes"][0]
    return {
        "distance_m": round(rt["distance"], 1),
        "duration_s": round(rt["duration"], 1),
        "geometry": rt["geometry"],
        "snapped": [w["location"] for w in data["waypoints"]],   # [lng, lat] on road
    }


async def nearest(lat: float, lng: float) -> dict | None:
    """Closest point on the road network, or None if nothing is near."""
    try:
        r = await client.get(f"{OSRM_URL}/nearest/v1/{OSRM_PROFILE}/{lng:.6f},{lat:.6f}", params={"number": 1})
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.error("OSRM unreachable: %s", e)
        raise HTTPException(503, "Routing engine is not reachable")
    if data.get("code") != "Ok" or not data.get("waypoints"):
        return None
    w = data["waypoints"][0]
    return {"location": w["location"], "distance_m": round(w.get("distance", 0), 1),
            "road_name": w.get("name") or None}


async def healthy() -> bool:
    try:
        r = await client.get(f"{OSRM_URL}/nearest/v1/{OSRM_PROFILE}/76.2673,9.9312")
        return r.json().get("code") == "Ok"
    except Exception:
        return False
