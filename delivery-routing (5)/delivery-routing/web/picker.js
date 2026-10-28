/*!
 * Delivery location picker. Drop-in for an address form.
 *
 *   <div id="delivery-pin" style="height:360px"></div>
 *   <input type="hidden" name="lat" id="lat"> <input type="hidden" name="lng" id="lng">
 *   <script src="https://YOUR-MAP-SERVER/picker.js"></script>
 *   <script>
 *     const picker = DRPicker.mount("#delivery-pin", {
 *       initial: { lat: 9.9816, lng: 76.2999 },      // saved location (optional)
 *       latInput: "#lat", lngInput: "#lng",           // auto-fill these fields (optional)
 *       onChange: (loc) => console.log(loc),          // every time the pin settles
 *       // search: false              hide the address search box
 *       // searchInput: "#line1"      give suggestions under YOUR address field instead of the box
 *       // initialSearch: "12/345 Lulu Road, Edappally"   start at the best match for a typed address
 *       // onSelect: (suggestion) => {}                    fill city / postcode fields from a suggestion
 *       // showCoordinates: true, autoLocate: false, confirmButton: true, onConfirm: fn,
 *       // text: { title: "Deliver here", locate: "My location", ... }   (labels / translations)
 *     });
 *     // picker.getValue()  -> { lat, lng, source, accuracy_m, road_name, road_distance_m, in_service_area }
 *   </script>
 *
 * The customer searches an area / street / landmark, drags the map under a fixed centre pin
 * (or taps a spot), or presses "Use my location". The value includes the address found at
 * the pin (road, locality, city, district, state, postcode). Coordinates are rounded to 6 decimals.
 */
(function () {
  "use strict";
  if (window.DRPicker) return;

  const SCRIPT = document.currentScript;
  const BASE = SCRIPT ? new URL(".", SCRIPT.src).href : new URL(".", location.href).href;
  const LIBS = {
    css: "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css",
    maplibre: "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js",
    pmtiles: "https://unpkg.com/pmtiles@3.2.1/dist/pmtiles.js",
  };

  // ------------------------------------------------------------ loading --
  const loaded = {};
  function loadScript(src) {
    return loaded[src] || (loaded[src] = new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = src; s.async = true;
      s.onload = ok; s.onerror = () => fail(new Error("Could not load " + src));
      document.head.appendChild(s);
    }));
  }
  function loadCss(href) {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = href;
    document.head.appendChild(l);
  }
  async function ready() {
    loadCss(LIBS.css);
    injectStyles();
    if (!window.maplibregl) await loadScript(LIBS.maplibre);
    if (!window.pmtiles) await loadScript(LIBS.pmtiles);
    if (!window.drMapStyle) await loadScript(BASE + "map-style.js");
    if (!window.DR_CONFIG) await loadScript(BASE + "config.js");
    if (!window.__drPmtiles) {
      window.__drPmtiles = new pmtiles.Protocol();
      maplibregl.addProtocol("pmtiles", window.__drPmtiles.tile);
    }
    const c = window.DR_CONFIG || {};
    return {
      tilesUrl: new URL(c.tilesUrl || "tiles/map.pmtiles", BASE).href,
      apiUrl: new URL(c.apiUrl || "api", BASE).href.replace(/\/$/, ""),
      center: c.center || [76.27, 10.0],
      zoom: c.zoom || 8,
      glyphs: c.glyphs,
    };
  }

  // ------------------------------------------------------------- styles --
  function injectStyles() {
    if (document.getElementById("drp-styles")) return;
    const st = document.createElement("style");
    st.id = "drp-styles";
    st.textContent = `
.drp{position:relative;width:100%;height:100%;min-height:260px;overflow:hidden;border-radius:inherit;
  font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1d1a;background:#f4f1ea}
.drp-map{position:absolute;inset:0}
.drp-pin{position:absolute;left:50%;top:50%;width:36px;height:48px;margin:-48px 0 0 -18px;pointer-events:none;
  transition:transform .15s ease;z-index:2;filter:drop-shadow(0 3px 4px rgba(0,0,0,.35))}
.drp-moving .drp-pin{transform:translateY(-10px)}
.drp-dot{position:absolute;left:50%;top:50%;width:8px;height:4px;margin:-2px 0 0 -4px;border-radius:50%;
  background:rgba(0,0,0,.35);z-index:1;pointer-events:none;transition:transform .15s ease}
.drp-moving .drp-dot{transform:scale(1.6)}
.drp-bar{position:absolute;left:10px;right:10px;bottom:10px;z-index:3;display:flex;gap:8px;align-items:center;
  background:#fffdf9;border:1px solid #e4ded3;border-radius:12px;padding:9px 10px 9px 12px;box-shadow:0 4px 14px rgba(40,30,10,.14)}
.drp-msg{flex:1;min-width:0}
.drp-msg b{display:block;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.drp-msg span{display:block;font-size:12px;color:#6e675d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.drp-msg .drp-warn{color:#c0392b}
.drp-btn{flex:none;border:0;border-radius:9px;padding:9px 12px;font:600 13px system-ui,sans-serif;cursor:pointer;
  background:#1f1d1a;color:#fff;display:flex;align-items:center;gap:6px}
.drp-btn.drp-ghost{background:#f1ece3;color:#1f1d1a}
.drp-btn:disabled{opacity:.55;cursor:wait}
.drp-confirm{position:absolute;left:10px;right:10px;bottom:72px;z-index:3;width:auto;justify-content:center;padding:13px;font-size:15px;
  background:#1f9d6b}
.drp-hint{position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:3;background:rgba(31,29,26,.82);color:#fff;
  font-size:12.5px;padding:6px 11px;border-radius:16px;white-space:nowrap;pointer-events:none;transition:opacity .3s}
.drp .maplibregl-ctrl-bottom-right{bottom:62px}
.drp-search{position:absolute;left:10px;right:10px;top:10px;z-index:4}
.drp-search input{width:100%;box-sizing:border-box;padding:11px 36px 11px 38px;border:1px solid #e4ded3;border-radius:11px;
  font:15px system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1d1a;background:#fffdf9;box-shadow:0 4px 14px rgba(40,30,10,.14);outline:none}
.drp-search input::-webkit-search-cancel-button{-webkit-appearance:none;display:none}
.drp-search input:focus{border-color:#e8742a;box-shadow:0 0 0 3px rgba(232,116,42,.18),0 4px 14px rgba(40,30,10,.14)}
.drp-sicon{position:absolute;left:12px;top:22px;transform:translateY(-50%);color:#6e675d;pointer-events:none;display:flex}
.drp-clear{position:absolute;right:5px;top:22px;transform:translateY(-50%);border:0;background:none;color:#6e675d;cursor:pointer;
  width:30px;height:30px;font-size:20px;line-height:1;border-radius:8px}
.drp-clear:hover{background:#f1ece3}
.drp-list{list-style:none;margin:6px 0 0;padding:4px;background:#fffdf9;border:1px solid #e4ded3;border-radius:11px;
  box-shadow:0 10px 28px rgba(40,30,10,.18);max-height:288px;overflow:auto;font:14px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1d1a}
.drp-list[hidden]{display:none}
.drp-list li{padding:8px 9px;border-radius:8px;cursor:pointer;display:flex;gap:10px;align-items:flex-start}
.drp-list li[aria-selected="true"],.drp-list li:hover{background:#f1ece3}
.drp-list li.drp-empty{color:#6e675d;cursor:default;background:none;font-size:13px}
.drp-ic{flex:none;width:26px;height:26px;border-radius:7px;background:#f1ece3;display:grid;place-items:center;color:#6e675d}
.drp-list li[aria-selected="true"] .drp-ic{background:#fff}
.drp-li-t{min-width:0}
.drp-list b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.drp-list b mark{background:none;color:inherit;font-weight:700}
.drp-list small{display:block;color:#6e675d;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.drp-list.drp-floating{position:absolute;z-index:2147483000;margin:0;box-sizing:border-box}
.drp.drp-has-search .drp-hint{top:64px}`;
    document.head.appendChild(st);
  }

  const PIN_SVG = `<svg viewBox="0 0 36 48" width="36" height="48" aria-hidden="true">
    <path d="M18 1C8.6 1 1 8.4 1 17.6 1 30 18 47 18 47s17-17 17-29.4C35 8.4 27.4 1 18 1z" fill="#e8742a" stroke="#fff" stroke-width="2"/>
    <circle cx="18" cy="17.5" r="6.5" fill="#fff"/></svg>`;
  const GPS_SVG = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor"
    stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>`;

  const ICONS = {
    place: '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    street: '<path d="M8 3 5 21M16 3l3 18M12 4v3M12 11v3M12 18v2"/>',
    poi: '<path d="M4 21V8l8-5 8 5v13"/><path d="M9 21v-6h6v6"/>',
    address: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
  };
  const icon = (k) => `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[k] || ICONS.place}</svg>`;
  const SEARCH_SVG = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2"
    aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function highlight(text, q) {
    const words = q.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1);
    let out = esc(text);
    for (const w of words) {
      const re = new RegExp("(^|[^\\p{L}\\p{N}])(" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "giu");
      out = out.replace(re, "$1<mark>$2</mark>");
    }
    return out;
  }

  // -------------------------------------------------------------- mount --
  function mount(target, opts = {}) {
    const root = typeof target === "string" ? document.querySelector(target) : target;
    if (!root) throw new Error("DRPicker: element not found: " + target);
    const t = Object.assign({
      move: "Move the map to place the pin on the delivery spot",
      locate: "Use my location",
      locating: "Locating…",
      near: "Near",
      noRoad: "No road nearby",
      outside: "Sorry, we don't deliver to this location yet",
      gpsDenied: "Location permission denied. Move the map instead.",
      gpsFailed: "Couldn't get your location. Move the map instead.",
      confirm: "Confirm location",
      title: "Delivery location",
      searchPlaceholder: "Search area, street or landmark",
      noResults: "No matches. Try a nearby landmark, or move the map.",
      adjust: "Now move the map so the pin is on your exact door",
    }, opts.text || {});

    root.innerHTML = "";
    const box = document.createElement("div");
    box.className = "drp";
    box.innerHTML = `<div class="drp-map"></div><div class="drp-dot"></div><div class="drp-pin">${PIN_SVG}</div>
      <div class="drp-hint">${t.move}</div>
      ${opts.search !== false && !opts.searchInput ? `<div class="drp-search">
        <span class="drp-sicon">${SEARCH_SVG}</span>
        <input type="search" class="drp-q" autocomplete="off" enterkeyhint="search" placeholder="${esc(t.searchPlaceholder)}">
        <button type="button" class="drp-clear" aria-label="Clear" hidden>&times;</button>
        <ul class="drp-list" hidden></ul></div>` : ""}
      ${opts.confirmButton ? `<button type="button" class="drp-btn drp-confirm">${t.confirm}</button>` : ""}
      <div class="drp-bar"><div class="drp-msg" aria-live="polite"><b>–</b><span></span></div>
        <button type="button" class="drp-btn drp-ghost drp-gps" aria-label="${t.locate}">${GPS_SVG}<span>${t.locate}</span></button></div>`;
    root.appendChild(box);
    if (opts.search !== false && !opts.searchInput) box.classList.add("drp-has-search");
    const $ = (sel) => box.querySelector(sel);

    let map, cfg, value = null, lastSource = opts.initial ? "initial" : "default", accuracy = null;
    let nearestCtl = null, nearestTimer = null, destroyed = false;
    let selected = null, hintAfterMove = false, reverseOn = opts.reverse !== false, searchUi = null;
    const listeners = [];

    const round6 = (n) => Math.round(n * 1e6) / 1e6;
    const fill = (sel, v) => {
      const el = sel && (typeof sel === "string" ? document.querySelector(sel) : sel);
      if (el) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); }
    };

    function msg(title, sub, warn) {
      $(".drp-msg b").textContent = title;
      const s = $(".drp-msg span");
      s.textContent = sub || "";
      s.className = warn ? "drp-warn" : "";
    }

    function emit() {
      fill(opts.latInput, value.lat);
      fill(opts.lngInput, value.lng);
      const out = Object.assign({}, value);
      if (typeof opts.onChange === "function") opts.onChange(out);
      root.dispatchEvent(new CustomEvent("dr-location", { detail: out, bubbles: true }));
      listeners.forEach((f) => f(out));
    }

    function settle() {
      if (destroyed) return;
      const c = map.getCenter();
      value = {
        lat: round6(c.lat), lng: round6(c.lng), source: lastSource,
        accuracy_m: lastSource === "gps" ? accuracy : null,
        road_name: null, road_distance_m: null, in_service_area: null,
        display: null, address: null,
        search_result: lastSource === "search" ? selected : null,
      };
      msg(titleText(), "…");
      emit();
      clearTimeout(nearestTimer);
      nearestTimer = setTimeout(lookupRoad, 350);
    }

    async function lookupRoad() {
      if (!value || opts.roadLookup === false) { msg(titleText(), ""); return; }
      nearestCtl?.abort();
      nearestCtl = new AbortController();
      const v = value, signal = nearestCtl.signal;
      const get = (path) => fetch(`${cfg.apiUrl}${path}`, { signal }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
      const [near, rev] = await Promise.allSettled([
        get(`/nearest?lat=${v.lat}&lng=${v.lng}`),
        reverseOn ? get(`/geocode/reverse?lat=${v.lat}&lng=${v.lng}`) : Promise.reject("off"),
      ]);
      if (v !== value || destroyed) return;            // pin moved again meanwhile
      if (rev.status === "rejected" && rev.reason === 503) reverseOn = false;   // search data not built
      const d = near.status === "fulfilled" ? near.value : null;
      const a = rev.status === "fulfilled" ? rev.value : null;
      if (d) {
        value.in_service_area = d.in_service_area;
        value.road_name = d.road?.name || a?.address?.road || null;
        value.road_distance_m = d.road?.distance_m ?? null;
      }
      if (a && (!d || d.in_service_area)) { value.display = a.display; value.address = a.address; }
      if (d && !d.in_service_area) msg(titleText(), t.outside, true);
      else if (a?.display) msg(titleText(), a.display);
      else if (value.road_name) msg(titleText(), `${t.near} ${value.road_name}`);
      else msg(titleText(), d && !d.road ? t.noRoad : "", d && !d.road);
      emit();
    }
    // set showCoordinates: true to show the numbers to the customer
    const titleText = () => opts.showCoordinates && value ? `${value.lat.toFixed(6)}, ${value.lng.toFixed(6)}` : t.title;

    function locateMe() {
      if (!navigator.geolocation) { msg(titleText(), t.gpsFailed, true); return; }
      const btn = $(".drp-gps"), label = btn.querySelector("span");
      btn.disabled = true; label.textContent = t.locating;
      navigator.geolocation.getCurrentPosition((p) => {
        btn.disabled = false; label.textContent = t.locate;
        lastSource = "gps"; accuracy = Math.round(p.coords.accuracy);
        map.flyTo({ center: [p.coords.longitude, p.coords.latitude], zoom: Math.max(map.getZoom(), 17), speed: 1.6, maxDuration: 1500 });
      }, (err) => {
        btn.disabled = false; label.textContent = t.locate;
        msg(value ? titleText() : t.move, err.code === 1 ? t.gpsDenied : t.gpsFailed, true);
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 });
    }

    // --------------------------------------------------------- search --
    async function searchApi(q, limit, signal) {
      if (!cfg) return [];
      q = (q || "").trim();
      if (q.length < 2) return [];
      const c = map ? map.getCenter() : null;
      const near = c ? `&lat=${c.lat.toFixed(5)}&lng=${c.lng.toFixed(5)}` : "";
      const r = await fetch(`${cfg.apiUrl}/geocode/search?q=${encodeURIComponent(q)}&limit=${limit || opts.searchLimit || 6}${near}`, { signal });
      if (r.status === 503) { searchUi?.disable(); return []; }
      if (!r.ok) throw new Error("search failed: " + r.status);
      return r.json();
    }

    function choose(sug) {
      selected = sug;
      lastSource = "search";
      hintAfterMove = true;
      map.flyTo({ center: [sug.lng, sug.lat], zoom: sug.zoom || 17, speed: 1.8, maxDuration: 1500 });
      if (typeof opts.onSelect === "function") opts.onSelect(Object.assign({}, sug));
    }

    function setupSearch() {
      if (opts.search === false) return;
      const external = !!opts.searchInput;
      const input = external
        ? (typeof opts.searchInput === "string" ? document.querySelector(opts.searchInput) : opts.searchInput)
        : $(".drp-q");
      if (!input) { console.warn("DRPicker: searchInput not found:", opts.searchInput); return; }
      const list = external ? document.createElement("ul") : $(".drp-list");
      const clear = external ? null : $(".drp-clear");
      if (external) { list.className = "drp-list drp-floating"; list.hidden = true; document.body.appendChild(list); }
      const listId = "drp-list-" + Math.random().toString(36).slice(2, 8);
      list.id = listId; list.setAttribute("role", "listbox");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-controls", listId);
      input.setAttribute("aria-expanded", "false");
      if (external) input.setAttribute("autocomplete", "off");

      let items = [], active = -1, ctl = null, timer = null, lastQ = "", disabled = false;

      const place = () => {
        if (!external) return;
        const r = input.getBoundingClientRect();
        list.style.left = r.left + scrollX + "px";
        list.style.top = r.bottom + scrollY + 4 + "px";
        list.style.width = r.width + "px";
      };
      const open = () => { if (disabled) return; place(); list.hidden = false; input.setAttribute("aria-expanded", "true"); };
      const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
      const setActive = (i) => {
        active = i;
        [...list.children].forEach((li, j) => li.setAttribute("aria-selected", j === i ? "true" : "false"));
        const li = list.children[i];
        if (li) { li.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", li.id); }
      };
      const render = (q) => {
        list.innerHTML = items.length
          ? items.map((s, i) => `<li role="option" id="${listId}-${i}" data-i="${i}" aria-selected="false">
              <span class="drp-ic">${icon(s.kind)}</span>
              <span class="drp-li-t"><b>${highlight(s.name, q)}</b><small>${esc(s.subtitle || s.type)}</small></span></li>`).join("")
          : `<li class="drp-empty" role="option" aria-disabled="true">${esc(t.noResults)}</li>`;
        open();
      };
      const pick = (i) => {
        const s = items[i];
        if (!s) return;
        close();
        if (opts.fillSearchInput !== false) {
          input.value = external ? [s.name, s.subtitle].filter(Boolean).join(", ") : s.name;
          lastQ = input.value.trim();
          if (clear) clear.hidden = !input.value;
        }
        choose(s);
      };
      const run = async () => {
        const q = input.value.trim();
        if (clear) clear.hidden = !q;
        if (q.length < 2) { items = []; close(); return; }
        if (q === lastQ) { if (items.length) open(); return; }
        lastQ = q;
        ctl?.abort(); ctl = new AbortController();
        try {
          items = await searchApi(q, null, ctl.signal);
          if (q === input.value.trim() && !disabled) render(q);
        } catch (e) { if (e.name !== "AbortError") console.warn("DRPicker:", e.message); }
      };

      const onInput = () => { clearTimeout(timer); timer = setTimeout(run, 220); };
      const onKey = (e) => {
        if (e.key === "Enter" && !external && (list.hidden || !items.length)) {   // the box is not part of your form
          e.preventDefault(); clearTimeout(timer); run(); return;
        }
        if (list.hidden || !items.length) {
          if (e.key === "ArrowDown" && items.length) { open(); setActive(0); e.preventDefault(); }
          return;
        }
        if (e.key === "ArrowDown") { setActive((active + 1) % items.length); e.preventDefault(); }
        else if (e.key === "ArrowUp") { setActive((active - 1 + items.length) % items.length); e.preventDefault(); }
        else if (e.key === "Enter") { pick(active >= 0 ? active : 0); e.preventDefault(); }   // don't submit the form
        else if (e.key === "Escape") { close(); e.preventDefault(); }
      };
      const onFocus = () => { if (items.length && input.value.trim() === lastQ) open(); };
      const onBlur = () => setTimeout(close, 150);
      const onDown = (e) => {
        const li = e.target.closest("li[data-i]");
        if (li) { e.preventDefault(); pick(+li.dataset.i); }     // mousedown: before the input blurs
      };
      input.addEventListener("input", onInput);
      input.addEventListener("keydown", onKey);
      input.addEventListener("focus", onFocus);
      input.addEventListener("blur", onBlur);
      list.addEventListener("mousedown", onDown);
      if (clear) clear.onclick = () => { input.value = ""; items = []; lastQ = ""; clear.hidden = true; close(); input.focus(); };
      if (external) { addEventListener("resize", place); addEventListener("scroll", place, true); }

      searchUi = {
        disable() {                                       // address search data not built on the server
          disabled = true; close();
          if (!external) { $(".drp-search").remove(); box.classList.remove("drp-has-search"); }
        },
        destroy() {
          input.removeEventListener("input", onInput); input.removeEventListener("keydown", onKey);
          input.removeEventListener("focus", onFocus); input.removeEventListener("blur", onBlur);
          if (external) { list.remove(); removeEventListener("resize", place); removeEventListener("scroll", place, true); }
        },
      };
    }

    const api = {
      getValue: () => (value ? Object.assign({}, value) : null),
      setValue(lat, lng, zoom) {
        lastSource = "set";
        map ? map.jumpTo({ center: [lng, lat], zoom: zoom || Math.max(map.getZoom(), 17) }) : (opts.initial = { lat, lng });
      },
      locateMe: () => map && locateMe(),
      /** Suggestions for any text, e.g. the address the customer typed in your form. */
      search: (q, limit) => searchApi(q, limit),
      /** Move the pin to the best match for a typed address. Resolves to the suggestion, or null. */
      async findAddress(q) {
        const list = await searchApi(q, 1).catch(() => []);
        if (list[0]) choose(list[0]);
        return list[0] || null;
      },
      on(fn) { listeners.push(fn); return api; },
      destroy() { destroyed = true; searchUi?.destroy(); map?.remove(); root.innerHTML = ""; },
      get map() { return map; },
    };

    ready().then((c) => {
      if (destroyed) return;
      cfg = c;
      const start = opts.initial && isFinite(opts.initial.lat) && isFinite(opts.initial.lng)
        ? { center: [opts.initial.lng, opts.initial.lat], zoom: opts.zoom || 17 }
        : { center: opts.center || cfg.center, zoom: opts.zoom || cfg.zoom };
      map = new maplibregl.Map({
        container: $(".drp-map"), style: drMapStyle(cfg.tilesUrl, cfg.glyphs),
        center: start.center, zoom: start.zoom, maxPitch: 0, dragRotate: false,
        attributionControl: { compact: true }, cooperativeGestures: !!opts.cooperativeGestures,
      });
      map.touchZoomRotate.disableRotation();
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), opts.confirmButton ? "top-right" : "bottom-right");

      map.on("movestart", (e) => {
        box.classList.add("drp-moving");
        $(".drp-hint").style.opacity = 0;
        if (e.originalEvent) lastSource = "user";      // moved by the customer, not by code
      });
      map.on("moveend", () => {
        box.classList.remove("drp-moving");
        if (hintAfterMove) { hintAfterMove = false; const h = $(".drp-hint"); h.textContent = t.adjust; h.style.opacity = 1; }
        settle();
      });
      map.on("click", (e) => { lastSource = "user"; map.easeTo({ center: e.lngLat, duration: 400 }); });
      $(".drp-gps").onclick = locateMe;
      if (opts.confirmButton) $(".drp-confirm").onclick = () => {
        if (!value) return;
        if (value.in_service_area === false) { msg(titleText(), t.outside, true); return; }
        opts.onConfirm?.(Object.assign({}, value));
      };

      setupSearch();
      if (opts.houseLabels !== false) drAddressLabels(map, cfg.apiUrl);   // house names / numbers when zoomed in
      map.once("style.load", async () => {
        settle();
        if (!opts.initial && opts.initialSearch && await api.findAddress(opts.initialSearch)) return;
        if (!opts.initial && opts.autoLocate !== false) locateMe();
      });
    }).catch((e) => msg("Map failed to load", e.message, true));

    return api;
  }

  /** Send a message to whatever app hosts this page (iframe parent, Flutter, Android, iOS, React Native). */
  function sendToApp(message) {
    const json = JSON.stringify(message);
    try { if (window.parent !== window) window.parent.postMessage(message, "*"); } catch (_) {}
    try { window.DrBridge?.postMessage(json); } catch (_) {}                         // Flutter JavaScriptChannel
    try { window.DrAndroid?.postMessage(json); } catch (_) {}                        // Android addJavascriptInterface
    try { window.webkit?.messageHandlers?.drBridge?.postMessage(message); } catch (_) {} // iOS WKScriptMessageHandler
    try { window.ReactNativeWebView?.postMessage(json); } catch (_) {}               // React Native WebView
  }

  window.DRPicker = { mount, sendToApp, version: "1.1" };
})();
