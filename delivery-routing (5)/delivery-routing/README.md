# Delivery Routing

Self-hosted maps, routing and live tracking for instant orders: **rider → pickup → customer**.
Built from one or more OpenStreetMap `.osm.pbf` region files (e.g. Geofabrik's India zones).

| Container | What it does | Port |
|---|---|---|
| `dr-osrm` | Road routing engine (OSRM) | 127.0.0.1:5005 |
| `dr-api` | Python (FastAPI) routing + tracking API | 127.0.0.1:8001 |
| `dr-postgis` | Its own PostGIS for routes and tracking | 127.0.0.1:5442 |
| `dr-web` | Map pages, `.pmtiles`, proxies `/api` | 8088 |

No clash with Airflow: project `delivery-routing`, network `dr-routing-net`, DB `dr_routing`,
user `dr_routing_user`, port 5442. Airflow's `postgres` / `airflow` / 5432 / 8080 are untouched.

## Setup

```bash
cp .env.example .env                  # set DR_DB_PASSWORD (and DR_DATA_DIR if you like)
mkdir -p data/osm
cp /path/to/southern-zone-261024.osm.pbf data/osm/
./scripts/setup.sh                    # routing data, map tiles, address search (needs internet for tiles)
docker compose up -d --build
```

Open **http://SERVER:8088**. API docs: **http://SERVER:8088/api/docs**

## All data stays on the host

Everything lives in `DR_DATA_DIR` (default `./data`). No Docker volumes are used, so you can remove
or rebuild containers freely and back up one folder.

```
data/
  osm/            your .osm.pbf region files  <- the only thing you manage
  work/           merged regions + covered area (bbox.txt)
  osrm/           routing graph
  tiles/          map.pmtiles base map
  tiles-sources/  coastline / Natural Earth downloads (cached)
  postgres/       the dr_routing database
```

**Upgrading from an earlier version** (database in the `dr-routing-pgdata` volume):
run `./scripts/move-db-to-host.sh` once, then `./scripts/setup.sh` (rebuilds the map data under the new
names) and `docker compose up -d --build`.

## Adding another state or region

1. Download the extra region, e.g. `https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf`.
2. Copy it into `data/osm/` next to the existing file.
3. Run `./scripts/setup.sh`.

All files in `data/osm/` are merged into one map (roads crossing a border connect), the routing graph,
tiles and address search are rebuilt, and the engine restarts. The stack keeps serving the old data until the new
build is ready. The accepted coordinate area grows automatically.

- **Update** a region: replace its file with a newer download, run `./scripts/setup.sh`.
- **Remove** a region: delete its file, run `./scripts/setup.sh`.
- Unchanged steps are skipped. `DR_FORCE_REBUILD=1` rebuilds everything once.
- Download all regions **on the same day** so shared border roads match.
- **Memory:** OSRM needs roughly 8–12 GB RAM for one Geofabrik India zone, more for several.
  Add swap if needed. Running needs ~3–4 GB.

## API

Full reference: **`docs/openapi.yaml`** (OpenAPI 3.0, import into Swagger UI / Postman / code generators)
and **`docs/delivery-routing.postman_collection.json`** (Postman, runnable end to end; set `baseUrl`).
Run the collection from the command line with `npx newman run docs/delivery-routing.postman_collection.json --env-var baseUrl=http://SERVER:8088`.

### `POST /api/order-route`

```bash
curl -X POST http://localhost:8001/api/order-route \
  -H 'Content-Type: application/json' \
  -d '{
    "order_id": "ORD-1001",
    "rider":  {"lat": 9.9312,  "lng": 76.2673},
    "pickup": {"lat": 9.9816,  "lng": 76.2999},
    "drop":   {"lat": 10.0159, "lng": 76.3320}
  }'
```

`rider` is optional. Response:

```json
{
  "order_id": "ORD-1001",
  "to_pickup": {"distance_m": 7421.3, "duration_s": 812.4},
  "to_drop":   {"distance_m": 5630.0, "duration_s": 640.1},
  "total":     {"distance_m": 13051.3, "duration_s": 1452.5},
  "snapped":   {"rider": [76.26, 9.93], "pickup": [...], "drop": [...]},
  "geojson":   {"type": "FeatureCollection", "features": [ ...two LineStrings... ]},
  "saved_id": 42
}
```

`snapped` and GeoJSON coordinates are `[lng, lat]` (GeoJSON order).

### Other endpoints

- `GET /api/order-route/{order_id}` – latest saved route for an order
- `POST /api/route` – `{"points": [{lat,lng}, ...]}` route through 2–25 points in order
- `GET /api/health` – OSRM + DB status
- `POST /api/orders/{id}/location`, `POST /api/orders/{id}/pickup`, `POST /api/orders/{id}/deliver`, `GET /api/riders/{rider_id}/orders`, `POST /api/orders/{id}/status`, `GET /api/orders/{id}/live`, `WS /api/orders/{id}/ws` – live tracking (below)

## Show a route in your app

Open or embed the map with URL parameters (`lat,lng`); it routes automatically:

```
http://SERVER:8088/?order=ORD-1001&rider=9.9312,76.2673&pickup=9.9816,76.2999&drop=10.0159,76.3320
```

Add `&embed=1` to hide the side panel (for an `<iframe>` in your dashboard).

## Live tracking (ride in progress)

1. **Plan the order with an `order_id`** (`POST /api/order-route`, as above). That registers it for tracking.
2. **Rider app sends GPS every 5–10 seconds:**

```bash
curl -X POST http://SERVER:8088/api/orders/ORD-1001/location \
  -H 'Content-Type: application/json' \
  -d '{"lat": 9.9500, "lng": 76.2800, "heading": 40, "speed": 8.3, "accuracy": 12, "rider_id": "RIDER-7"}'
```

Only `lat` and `lng` are required. `heading` is in degrees (0 = north) and turns the rider arrow.
`speed` is in m/s, and `accuracy` is in metres. Fixes worse than 150 m are stored but don't move the rider.
The response gives the remaining distance, time and ETA, plus `arrived: true` within 60 m of the target.

3. **Rider confirms pickup and delivery** (order ID in the URL, rider ID in the body):

```bash
curl -X POST http://SERVER:8088/api/orders/ORD-1001/pickup \
  -H 'Content-Type: application/json' \
  -d '{"rider_id": "RIDER-7", "lat": 9.9818, "lng": 76.3001}'

curl -X POST http://SERVER:8088/api/orders/ORD-1001/deliver \
  -H 'Content-Type: application/json' \
  -d '{"rider_id": "RIDER-7", "lat": 10.0157, "lng": 76.3318, "note": "OTP 4821 verified"}'
```

The server checks:

| Check | If it fails |
|---|---|
| `rider_id` is the rider assigned to the order (an order created without one is claimed by the first rider to confirm) | `403` |
| Delivery only after pickup; nothing after `cancelled` | `409` |
| Rider within `DR_COMPLETE_RADIUS_M` (default 250 m) of the pickup / customer | `422` with the distance |

`lat`/`lng` are optional. Without them the rider's last GPS update (if under 3 minutes old) is used.
With no position at all the confirmation is allowed, unless `DR_COMPLETE_REQUIRE_LOCATION=1`.
Tapping twice is safe: the second call returns `"already_done": true`. `note` is optional (receiver name, OTP, remarks).
Who confirmed, where, how far away and the note are saved in `dr_live_orders`.

Before pickup the route goes rider → pickup; after it, rider → customer.

**Rider's orders:** `GET /api/riders/RIDER-7/orders` lists the active ones (`?active=false` for history).

**Ops override** (no rider or location checks), for support staff only. Don't expose it to riders:

```bash
curl -X POST http://SERVER:8088/api/orders/ORD-1001/status -H 'Content-Type: application/json' -d '{"status": "cancelled"}'
```

4. **Show it:** load the map once and it updates itself over a WebSocket. No reloading needed.

```html
<iframe src="http://SERVER:8088/?order=ORD-1001&live=1&embed=1" style="width:100%;height:420px;border:0"></iframe>
```

Add `&card=0` to hide the ETA card if your app shows its own.

**Getting the ETA into your app.** The iframe posts every update to its parent:

```js
window.addEventListener("message", (e) => {
  if (e.data?.type === "dr:update") console.log(e.data.state.eta, e.data.state.remaining_m);
});
```

You can also read it directly from `GET /api/orders/{id}/live` or the WebSocket `ws://SERVER:8088/api/orders/{id}/ws`.

**Flutter (webview_flutter):** load the same URL. To receive updates, add a JavaScript channel named `DrBridge`:

```dart
controller
  ..setJavaScriptMode(JavaScriptMode.unrestricted)
  ..addJavaScriptChannel('DrBridge', onMessageReceived: (m) {
      final state = jsonDecode(m.message)['state'];   // eta, remaining_m, status, rider ...
  })
  ..loadRequest(Uri.parse('http://SERVER:8088/?order=ORD-1001&live=1&embed=1'));
```

**Testing:**
- `http://SERVER:8088/rider.html?order=ORD-1001&rider=RIDER-7` sends a phone's real GPS. Phones need **https** for GPS.
- `python3 scripts/simulate_ride.py ORD-1001 --api http://SERVER:8088` drives a fake rider along the real route.

Positions are stored in `dr_rider_locations`, and the current state of each order is in `dr_live_orders`.

## Address picker (customer pins their exact location)

Add to your address page. The customer **searches** an area, street or landmark (suggestions as they
type), or moves the map under the pin, or taps "Use my location". You get `lat`/`lng` plus the
address found at the pin to save with the customer's address.

```html
<div id="delivery-pin" style="height:380px"></div>
<input type="hidden" name="lat" id="lat">
<input type="hidden" name="lng" id="lng">

<script src="https://SERVER/picker.js"></script>
<script>
  const picker = DRPicker.mount("#delivery-pin", {
    initial: { lat: 9.9816, lng: 76.2999 },   // when editing a saved address; omit for a new one
    latInput: "#lat", lngInput: "#lng",        // filled in automatically
    onChange: (loc) => console.log(loc),       // every time the pin settles
  });
</script>
```

`picker.getValue()` / `onChange` give:

```json
{ "lat": 10.02725, "lng": 76.309, "source": "search",
  "display": "Near LuLu International Shopping Mall, Edappally, Kochi",
  "address": { "house_number": null, "road": null, "locality": "Edappally", "city": "Kochi",
               "district": "Ernakulam", "state": "Kerala", "postcode": "682024" },
  "in_service_area": true, "road_name": "...", "accuracy_m": null,
  "search_result": { "name": "LuLu International Shopping Mall", "...": "..." } }
```

- `source`: `search` (picked a suggestion), `user` (moved by hand), `gps`, `initial` or `set`.
- `in_service_area: false` means the pin is outside your loaded regions, so block saving it.
- `address` is the best guess at the pin, so use it to **pre-fill** city / state / PIN code fields and
  let the customer correct them (see the demo, which never overwrites what they typed).

**Address suggestions**

| Option | What it does |
|---|---|
| *(default)* | Search box on top of the map with suggestions |
| `searchInput: "#line1"` | Suggestions appear under **your own** address field instead (no box on the map); choosing one fills the field and moves the pin |
| `initialSearch: "12/345 Lulu Road, Edappally"` | For a new address the customer already typed: start the map at the best match |
| `onSelect: (s) => {}` | Called with the chosen suggestion (`name`, `subtitle`, `lat`, `lng`, `address`) |
| `search: false` | No address search |

Also `picker.search(text)` returns suggestions for any text, and `picker.findAddress(text)` moves the
pin to the best match. Suggestions are typo-tolerant ("edapally" finds Edappally) and prefer places near
the current map view. If the search data isn't built yet, the box simply doesn't show.

**What can be found** is exactly what your `.osm.pbf` files contain:

| Found | OpenStreetMap tags |
|---|---|
| Areas, towns, villages | `place=*` with a name |
| Streets | named roads |
| Landmarks, shops, hospitals, schools ... | named `amenity` / `shop` / `tourism` / `office` / ... |
| Named buildings, apartment blocks | `building=*` + `name` |
| Houses | `addr:housenumber`, `addr:housename` ("Rose Villa") or `addr:full` |

A building outline with no name and no address tag has nothing to search for, and in most of India
that is the majority of buildings. Check what your data has:

```bash
curl http://SERVER:8088/api/geocode/stats
# {"areas":..,"streets":..,"landmarks":..,"named_buildings":..,"addresses":..,"addresses_with_house_name":..}
```

When zoomed in to street level, the maps label house names / numbers and named buildings from this
data (shops and landmarks are labelled from the map tiles at zoom 15+).

Other options: `showCoordinates`, `autoLocate: false`, `zoom`, `center`, `text: {...}` for labels or
translations. Methods: `getValue()`, `setValue(lat, lng)`, `locateMe()`, `destroy()`.

- Allow your shop's domain in `.env`: `DR_CORS_ORIGINS=https://shop.example.com`.
- Live example: **http://SERVER:8088/picker-demo.html** (and `?mode=field`). View source to copy.
- Browsers only allow "Use my location" on **https** pages.

**Mobile apps:** open `https://SERVER/picker.html` (or `?lat=..&lng=..` to start at a saved spot) in a
WebView. "Confirm location" sends `{"type":"dr:location","lat":..,"lng":..,...}` to the app:

| App | How to receive |
|---|---|
| Flutter (webview_flutter) | `addJavaScriptChannel('DrBridge', onMessageReceived: (m) => jsonDecode(m.message))` |
| Android (WebView) | `addJavascriptInterface(obj, "DrAndroid")` with `@JavascriptInterface fun postMessage(json: String)` |
| iOS (WKWebView) | `userContentController.add(self, name: "drBridge")` → `message.body` |
| React Native | `<WebView onMessage={(e) => JSON.parse(e.nativeEvent.data)} />` |
| Web iframe | `window.addEventListener("message", e => e.data.type === "dr:location")` |

## Tracking page (map only)

For customers on your website or in your apps. Shows the route and the rider moving, with no
coordinates, forms or IDs on screen:

```
https://SERVER/track.html?order=ORD-1001          map only
https://SERVER/track.html?order=ORD-1001&eta=1    plus a small ETA card
```

```html
<iframe src="https://SERVER/track.html?order=ORD-1001" style="width:100%;height:420px;border:0"></iframe>
```

Load it once and it updates itself on every rider location (WebSocket, auto-reconnect). The view
keeps the rider and their next stop in frame. Before pickup the route goes to the pickup, after
pickup to the customer. Every update is also sent to the host app as `{"type":"dr:update","state":{...}}`
through the same bridges as the picker (`DrBridge`, `DrAndroid`, `drBridge`, React Native, iframe),
so your app can show its own ETA text.

## Notes

- **Two-wheelers:** set `DR_OSRM_PROFILE=bicycle` for a closer match on narrow roads, or copy
  OSRM's `car.lua` into a custom scooter profile. Rebuild after changing.
- **Fully offline:** the pages load MapLibre from unpkg and label fonts from GitHub.
  Download those and change the URLs in `web/*.html`, `web/picker.js` and `web/config.js`.
- **Production:** put `dr-web` behind your HTTPS reverse proxy; the API and OSRM only listen on localhost.
  Anyone with an order ID can open its tracking page, so use order IDs that can't be guessed
  (or a separate random tracking code as `order_id`).
- Saved routes live in table `dr_order_routes` (see `db/init.sql`). Address search data lives in schema
  `dr_geo` (rebuilt by `setup.sh`; set `DR_GEOCODE=0` to skip it). API: `/api/geocode/search`, `/api/geocode/reverse`.
