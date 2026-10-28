// Live tracking view: ?order=ORD-1001&live=1  (add &embed=1 for iframes, &card=0 to hide the ETA card)
//
// The page opens a WebSocket to /api/orders/{id}/ws and redraws on every update.
// Your app does NOT need to reload the iframe.
//
// Talking to the host page / Flutter WebView (optional):
//   - The map posts {type:"dr:update", state} on every change to: window.parent (iframe),
//     Flutter channel "DrBridge", Android interface "DrAndroid", iOS handler "drBridge", React Native.
//   - The host can move the rider instantly, without waiting for the server:
//       iframe.contentWindow.postMessage({type:"dr:location", lat, lng, heading}, "*")
//       or in Flutter: controller.runJavaScript("drLive.moveRider(9.95, 76.28, 40)")
(function () {
  const COLORS = { rider: "#3b6fd8", pickup: "#e8742a", drop: "#1f9d6b", trail: "#9a9388" };
  const STATUS_TEXT = {
    assigned: "Rider assigned", to_pickup: "Going to pickup", picked_up: "On the way to customer",
    delivered: "Delivered", cancelled: "Cancelled",
  };

  let map, orderId, ws, retry = 0, follow = true, riderMarker, lastState = null, anim = null;
  const pins = {};

  function riderEl() {
    const el = document.createElement("div");
    el.innerHTML = `<div class="dr-rider-halo"></div>
      <svg class="dr-rider-arrow" width="34" height="34" viewBox="0 0 34 34">
        <circle cx="17" cy="17" r="13" fill="${COLORS.rider}" stroke="#fff" stroke-width="3"/>
        <path d="M17 8 L23 22 L17 19 L11 22 Z" fill="#fff"/></svg>`;
    el.className = "dr-rider";
    return el;
  }

  function pinEl(kind, label) {
    const el = document.createElement("div");
    el.style.cssText = "width:28px;height:36px";
    el.innerHTML = `<div style="width:28px;height:28px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);
      background:${COLORS[kind]};border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35);display:grid;place-items:center">
      <span style="transform:rotate(45deg);color:#fff;font:700 12px system-ui">${label}</span></div>`;
    return el;
  }

  function setPin(kind, label, p) {
    if (!p) return;
    if (!pins[kind]) pins[kind] = new maplibregl.Marker({ element: pinEl(kind, label), anchor: "bottom" })
      .setLngLat([p.lng, p.lat]).addTo(map);
    else pins[kind].setLngLat([p.lng, p.lat]);
  }

  // smooth glide between GPS fixes so the rider doesn't jump
  function moveRider(lat, lng, heading) {
    if (lat == null || lng == null) return;
    if (!riderMarker) {
      riderMarker = new maplibregl.Marker({ element: riderEl(), rotationAlignment: "map" })
        .setLngLat([lng, lat]).addTo(map);
    }
    const from = riderMarker.getLngLat(), to = { lng, lat }, t0 = performance.now(), dur = 900;
    cancelAnimationFrame(anim);
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur), e = k * (2 - k);
      riderMarker.setLngLat([from.lng + (to.lng - from.lng) * e, from.lat + (to.lat - from.lat) * e]);
      if (k < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
    if (heading != null) riderMarker.setRotation(heading);
  }

  // keep the rider and where they are heading both in view
  function frame(s, animate) {
    const b = new maplibregl.LngLatBounds();
    const target = s.target === "pickup" ? s.pickup : s.target === "drop" ? s.drop : null;
    [s.rider, target].forEach((p) => p && b.extend([p.lng, p.lat]));
    if (b.isEmpty()) return;
    map.fitBounds(b, { padding: { top: 60, bottom: 90, left: 50, right: 50 }, maxZoom: 16, duration: animate ? 900 : 0 });
  }

  function render(s) {
    if (!s || s.type === "ping") return;
    if (s.error) return card(`<b>Order ${orderId}</b><div class="dr-sub">${s.error}</div>`);
    const first = !lastState;
    lastState = s;

    setPin("pickup", "P", s.pickup);
    setPin("drop", "C", s.drop);
    if (s.rider) moveRider(s.rider.lat, s.rider.lng, s.rider.heading);
    map.getSource("live")?.setData(s.geojson);
    map.setPaintProperty("live-remaining", "line-color", s.target === "pickup" ? COLORS.pickup : COLORS.drop);

    if (first) fitAll(s);
    else if (follow && s.rider && s.target) frame(s, true);

    const eta = s.eta ? new Date(s.eta).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;
    const mins = s.remaining_s != null ? Math.max(1, Math.round(s.remaining_s / 60)) : null;
    const km = s.remaining_m != null ? (s.remaining_m / 1000).toFixed(1) : null;
    const seen = s.rider?.last_seen ? Math.round((Date.now() - new Date(s.rider.last_seen)) / 1000) : null;
    const stale = seen != null && seen > 60;
    const done = s.status === "delivered" || s.status === "cancelled";
    card(`
      <div class="dr-status"><i style="background:${done ? COLORS.trail : s.target === "pickup" ? COLORS.pickup : COLORS.drop}"></i>
        ${STATUS_TEXT[s.status] || s.status}${s.arrived && !done ? " · <b>arrived</b>" : ""}</div>
      ${!done && mins != null ? `<div class="dr-eta"><b>${mins} min</b><span>${km} km · ETA ${eta}</span></div>` : ""}
      <div class="dr-sub">Order ${s.order_id}${s.rider_id ? " · " + s.rider_id : ""}${
        stale ? ` · <span class="dr-stale">last update ${Math.round(seen / 60)} min ago</span>` : ""}</div>`);

    const msg = { type: "dr:update", state: s };
    const json = JSON.stringify(msg);
    try { if (window.parent !== window) window.parent.postMessage(msg, "*"); } catch (_) {}
    try { window.DrBridge?.postMessage(json); } catch (_) {}                         // Flutter
    try { window.DrAndroid?.postMessage(json); } catch (_) {}                        // Android
    try { window.webkit?.messageHandlers?.drBridge?.postMessage(msg); } catch (_) {} // iOS
    try { window.ReactNativeWebView?.postMessage(json); } catch (_) {}               // React Native
  }

  function fitAll(s) {
    const b = new maplibregl.LngLatBounds();
    [s.pickup, s.drop, s.rider].forEach((p) => p && b.extend([p.lng, p.lat]));
    map.fitBounds(b, { padding: 70, maxZoom: 15, duration: 0 });
  }

  function card(html) {
    const el = document.getElementById("dr-live-card");
    if (el) el.innerHTML = html;
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const base = new URL(window.DR_CONFIG.apiUrl, location.href);
    ws = new WebSocket(`${proto}//${base.host}${base.pathname.replace(/\/$/, "")}/orders/${encodeURIComponent(orderId)}/ws`);
    ws.onopen = () => { retry = 0; };
    ws.onmessage = (e) => render(JSON.parse(e.data));
    ws.onclose = () => {                        // reconnect with backoff (phones sleep, networks drop)
      if (lastState && (lastState.status === "delivered" || lastState.status === "cancelled")) return;
      setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++));
    };
  }

  function start(theMap, id, opts = {}) {
    map = theMap; orderId = id;
    follow = opts.follow !== false;
    document.body.classList.add("live");
    if (opts.card === false) document.body.classList.add("no-card");

    map.addSource("live", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "live-trail", type: "line", source: "live", filter: ["==", ["get", "kind"], "trail"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": COLORS.trail, "line-width": 4, "line-opacity": 0.6 } });
    map.addLayer({ id: "live-remaining-casing", type: "line", source: "live", filter: ["==", ["get", "kind"], "remaining"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#fff", "line-width": 9 } });
    map.addLayer({ id: "live-remaining", type: "line", source: "live", filter: ["==", ["get", "kind"], "remaining"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": COLORS.pickup, "line-width": 5 } });

    // stop auto-following when the user pans; the button brings it back
    map.on("dragstart", () => { follow = false; document.getElementById("dr-recenter").hidden = false; });
    document.getElementById("dr-recenter").onclick = () => {
      follow = true; document.getElementById("dr-recenter").hidden = true;
      if (lastState) fitAll(lastState);
    };

    window.addEventListener("message", (e) => {
      const d = e.data;
      if (d && d.type === "dr:location") moveRider(d.lat, d.lng, d.heading);
    });
    setInterval(() => lastState && render(lastState), 30000);   // refresh "last update" age
    connect();
  }

  window.drLive = { start, moveRider };
})();
