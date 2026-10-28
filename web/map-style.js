// Light base map style for OpenMapTiles-schema tiles (what Planetiler builds).
window.drMapStyle = function (tilesUrl, glyphs) {
  const src = "omt";
  const road = (cls) => ["match", ["get", "class"], cls, true, false];
  const width = (stops) => ["interpolate", ["exponential", 1.5], ["zoom"], ...stops];
  const font = ["Noto Sans Regular"];
  const poiColor = ["match", ["get", "class"],
    ["hospital", "doctors", "pharmacy", "dentist"], "#c0392b",
    ["shop", "grocery", "clothing_store", "convenience", "supermarket", "fast_food", "restaurant", "cafe", "bakery"], "#b3591e",
    ["school", "college", "university", "library", "kindergarten"], "#6a5a99",
    ["bus", "railway", "airport", "fuel", "parking"], "#3b6fd8",
    ["place_of_worship", "religious"], "#7d6b52",
    "#6b6357"];

  return {
    version: 8,
    glyphs,
    sources: {
      [src]: {
        type: "vector",
        url: "pmtiles://" + new URL(tilesUrl, location.href).href,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#f4f1ea" } },

      { id: "landcover-wood", type: "fill", source: src, "source-layer": "landcover",
        filter: ["==", ["get", "class"], "wood"],
        paint: { "fill-color": "#d9e8cf", "fill-opacity": 0.8 } },
      { id: "landcover-grass", type: "fill", source: src, "source-layer": "landcover",
        filter: ["in", ["get", "class"], ["literal", ["grass", "farmland"]]],
        paint: { "fill-color": "#e6eed8", "fill-opacity": 0.7 } },
      { id: "landuse-residential", type: "fill", source: src, "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood"]]],
        paint: { "fill-color": "#ece7df", "fill-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0, 12, 1] } },
      { id: "park", type: "fill", source: src, "source-layer": "park",
        paint: { "fill-color": "#d4e6c6", "fill-opacity": 0.7 } },

      { id: "water", type: "fill", source: src, "source-layer": "water",
        paint: { "fill-color": "#a9d0e3" } },
      { id: "waterway", type: "line", source: src, "source-layer": "waterway",
        paint: { "line-color": "#a9d0e3", "line-width": width([8, 0.5, 14, 2.5]) } },

      { id: "building", type: "fill", source: src, "source-layer": "building", minzoom: 14,
        paint: { "fill-color": "#e0dad0", "fill-outline-color": "#d2cabd" } },

      { id: "boundary-state", type: "line", source: src, "source-layer": "boundary",
        filter: ["all", ["<=", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": "#a99fb5", "line-width": 1.2, "line-dasharray": [3, 2] } },

      // road casings
      { id: "road-major-casing", type: "line", source: src, "source-layer": "transportation",
        filter: road(["motorway", "trunk", "primary", "secondary"]), minzoom: 9,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#d8cbb4", "line-width": width([9, 1.5, 12, 4, 16, 18]) } },
      // roads
      { id: "road-minor", type: "line", source: src, "source-layer": "transportation",
        filter: road(["minor", "service", "track"]), minzoom: 12,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": width([12, 0.5, 16, 8]) } },
      { id: "road-tertiary", type: "line", source: src, "source-layer": "transportation",
        filter: road(["tertiary"]), minzoom: 10,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": width([10, 0.6, 16, 10]) } },
      { id: "road-major", type: "line", source: src, "source-layer": "transportation",
        filter: road(["primary", "secondary"]),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#fff3d6", "line-width": width([6, 0.4, 12, 3, 16, 14]) } },
      { id: "road-highway", type: "line", source: src, "source-layer": "transportation",
        filter: road(["motorway", "trunk"]),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#f6c97a", "line-width": width([5, 0.6, 12, 3.5, 16, 16]) } },
      { id: "rail", type: "line", source: src, "source-layer": "transportation",
        filter: ["==", ["get", "class"], "rail"], minzoom: 10,
        paint: { "line-color": "#b9b2a6", "line-width": 1, "line-dasharray": [4, 3] } },

      // labels
      { id: "road-label", type: "symbol", source: src, "source-layer": "transportation_name", minzoom: 13,
        layout: { "symbol-placement": "line", "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                  "text-font": font, "text-size": 11 },
        paint: { "text-color": "#6b6357", "text-halo-color": "#fff", "text-halo-width": 1.5 } },
      // shops, landmarks, hospitals ... (from the map tiles)
      { id: "poi-dot", type: "circle", source: src, "source-layer": "poi", minzoom: 15,
        filter: ["has", "name"],
        paint: { "circle-radius": 3.5, "circle-color": poiColor, "circle-stroke-color": "#fff", "circle-stroke-width": 1.2 } },
      { id: "poi-label", type: "symbol", source: src, "source-layer": "poi", minzoom: 15,
        filter: ["has", "name"],
        layout: { "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": font, "text-size": 11,
                  "text-anchor": "top", "text-offset": [0, 0.5], "text-max-width": 8, "text-optional": true },
        paint: { "text-color": poiColor, "text-halo-color": "#fff", "text-halo-width": 1.4 } },
      { id: "housenumber", type: "symbol", source: src, "source-layer": "housenumber", minzoom: 17,
        layout: { "text-field": ["get", "housenumber"], "text-font": font, "text-size": 10 },
        paint: { "text-color": "#8a8174", "text-halo-color": "#fff", "text-halo-width": 1 } },
      { id: "place-small", type: "symbol", source: src, "source-layer": "place",
        filter: ["in", ["get", "class"], ["literal", ["village", "suburb", "neighbourhood", "hamlet"]]], minzoom: 11,
        layout: { "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": font, "text-size": 11 },
        paint: { "text-color": "#6b6357", "text-halo-color": "#fff", "text-halo-width": 1.2 } },
      { id: "place-town", type: "symbol", source: src, "source-layer": "place",
        filter: ["==", ["get", "class"], "town"], minzoom: 8,
        layout: { "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": font,
                  "text-size": ["interpolate", ["linear"], ["zoom"], 8, 11, 14, 15] },
        paint: { "text-color": "#3f3a33", "text-halo-color": "#fff", "text-halo-width": 1.5 } },
      { id: "place-city", type: "symbol", source: src, "source-layer": "place",
        filter: ["==", ["get", "class"], "city"],
        layout: { "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": font,
                  "text-size": ["interpolate", ["linear"], ["zoom"], 5, 12, 12, 19] },
        paint: { "text-color": "#24211d", "text-halo-color": "#fff", "text-halo-width": 2 } },
    ],
  };
};


// House names / numbers and named buildings, from the address search data, drawn when zoomed in.
// (Map tiles don't carry house names like "Rose Villa".) Usage: drAddressLabels(map, "/api")
window.drAddressLabels = function (map, apiUrl, opts = {}) {
  const minZoom = opts.minZoom || 16.5;
  const empty = { type: "FeatureCollection", features: [] };
  let ctl = null, timer = null, disabled = false;
  const add = () => {
    if (map.getSource("dr-labels")) return;
    map.addSource("dr-labels", { type: "geojson", data: empty });
    map.addLayer({ id: "dr-labels-dot", type: "circle", source: "dr-labels", minzoom: minZoom,
      paint: { "circle-radius": 2.5, "circle-color": ["match", ["get", "kind"], "house", "#8a8174", "#6b6357"],
               "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
    map.addLayer({ id: "dr-labels-text", type: "symbol", source: "dr-labels", minzoom: minZoom,
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 10.5,
                "text-anchor": "top", "text-offset": [0, 0.4], "text-max-width": 7, "text-optional": true },
      paint: { "text-color": ["match", ["get", "kind"], "house", "#7a7166", "#4a443c"],
               "text-halo-color": "#fff", "text-halo-width": 1.3 } });
  };
  const load = async () => {
    if (disabled || map.getZoom() < minZoom) return;
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",");
    ctl?.abort(); ctl = new AbortController();
    try {
      const r = await fetch(`${apiUrl.replace(/\/$/, "")}/geocode/labels?bbox=${bbox}`, { signal: ctl.signal });
      if (r.status === 503) { disabled = true; return; }          // search data not built
      if (r.ok) map.getSource("dr-labels")?.setData(await r.json());
    } catch (_) { /* aborted or offline */ }
  };
  const start = () => { add(); load(); map.on("moveend", () => { clearTimeout(timer); timer = setTimeout(load, 250); }); };
  map.isStyleLoaded() ? start() : map.once("style.load", start);
};
