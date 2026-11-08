/* Sightline page runtime.
 *
 * Polls /api/status, draws one card per enabled widget, and hands each card to its renderer.
 * A widget with no renderer file is drawn by the generic renderer (summary, big numbers, meters, rows).
 *
 * Renderer API (web/widgets/<id>.js):
 *   Sightline.register("<id>", { render(data, ctx) { return "<html>" } });
 * `ctx` has helpers (escape, formatting, svg charts) and `ctx.widget` (the widget id).
 * Buttons need no wiring: <button data-action="stop" data-params='{"pid":1}' data-confirm>Stop</button>
 * calls the widget's "stop" action; data-confirm asks for a second click within 4 s.
 */
(function () {
  "use strict";
  const renderers = {};
  const cards = {};          // id -> {el, body, html}
  let meta = {};             // id -> widget metadata
  let lastStatus = null;
  let config = {};
  const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sightline;

  // Helpers ----------------------------------------------------------------------
  const h = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const attr = (o) => h(JSON.stringify(o || {}));
  const num = (v, d = 0) => (v == null || isNaN(v) ? "–" : Number(v).toFixed(d));
  const gb = (v, d = 1) => (v == null ? "–" : `${Number(v).toFixed(d)} GB`);
  const bytes = (n) => {
    if (n == null) return "–";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; n = Number(n);
    while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n >= 100 || i < 2 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
  };
  const ago = (ts) => {
    if (!ts) return "never";
    const m = Math.floor((Date.now() / 1000 - ts) / 60);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    if (m < 2880) return `${Math.floor(m / 60)} h ago`;
    return `${Math.floor(m / 1440)} days ago`;
  };
  const dur = (s) => {
    if (s == null) return "–";
    s = Math.round(s);
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min`;
    if (s < 172800) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
    return `${Math.floor(s / 86400)} days`;
  };
  const dot = (state) => `<span class="dot ${h(state || "off")}"></span>`;
  const stateOf = (pct, warn = 80, bad = 92) => (pct == null ? "off" : pct >= bad ? "bad" : pct >= warn ? "warn" : "ok");

  function spark(values, opts = {}) {
    const v = (values || []).filter((x) => x != null);
    const w = opts.width || 200, ht = opts.height || 26, color = opts.color || "var(--accent)";
    if (v.length < 2) return `<svg class="spark" viewBox="0 0 ${w} ${ht}" preserveAspectRatio="none"></svg>`;
    const max = opts.max != null ? opts.max : Math.max(...v, 1), min = opts.min != null ? opts.min : 0;
    const span = max - min || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${ht - 2 - ((x - min) / span) * (ht - 4)}`);
    const area = `M0,${ht} L${pts.join(" L")} L${w},${ht} Z`;
    return `<svg class="spark" viewBox="0 0 ${w} ${ht}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${area}" fill="${color}" opacity=".14"/><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function ring(pct, opts = {}) {
    const size = opts.size || 74, sw = opts.stroke || 7, r = (size - sw) / 2, c = 2 * Math.PI * r;
    const p = Math.max(0, Math.min(100, pct || 0));
    const color = opts.color || "var(--accent)";
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--bar)" stroke-width="${sw}"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round"
        stroke-dasharray="${(c * p) / 100} ${c}" transform="rotate(-90 ${size / 2} ${size / 2})"/>
      <text x="50%" y="50%" text-anchor="middle" dominant-baseline="central" fill="var(--text)" font-size="${size * 0.22}" font-weight="500">${opts.label != null ? h(opts.label) : pct == null ? "–" : Math.round(p) + "%"}</text></svg>`;
  }

  // parts: [{value, color, hatch?}] -> a donut. Total defaults to the sum.
  function pie(parts, opts = {}) {
    const size = opts.size || 104, sw = opts.stroke || 14, r = (size - sw) / 2, c = 2 * Math.PI * r;
    const total = opts.total || parts.reduce((a, p) => a + (p.value || 0), 0) || 1;
    let off = 0;
    const segs = parts.map((p) => {
      const len = (c * (p.value || 0)) / total;
      const s = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${p.color}" stroke-width="${p.thin ? sw * 0.55 : sw}"
        stroke-dasharray="${Math.max(0, len - 1)} ${c}" stroke-dashoffset="${-off}" transform="rotate(-90 ${size / 2} ${size / 2})"${p.opacity ? ` opacity="${p.opacity}"` : ""}/>`;
      off += len;
      return s;
    });
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--bar)" stroke-width="${sw}"/>${segs.join("")}
      ${opts.label ? `<text x="50%" y="46%" text-anchor="middle" fill="var(--text)" font-size="${size * 0.17}" font-weight="500">${h(opts.label)}</text>` : ""}
      ${opts.sub ? `<text x="50%" y="62%" text-anchor="middle" fill="var(--faint)" font-size="${size * 0.1}">${h(opts.sub)}</text>` : ""}</svg>`;
  }

  // Generic pieces ----------------------------------------------------------------------
  function actionButtons(actions) {
    return (actions || []).map((a) => `<button class="btn ${a.primary ? "primary" : "quiet"}" data-action="${h(a.id)}" data-params="${attr(a.params)}"${a.confirm ? " data-confirm" : ""}${a.widget ? ` data-widget="${h(a.widget)}"` : ""}>${h(a.label)}</button>`).join("");
  }
  function rows(list) {
    return (list || []).map((r) => {
      if (r.sep) return `<div class="sep">${h(r.sep)}</div>`;
      const label = r.sub ? `<span class="stack"><span class="lbl">${h(r.label)}</span><span class="sub">${h(r.sub)}</span></span>` : `<span class="lbl">${h(r.label)}</span>`;
      const indent = r.depth ? ` style="padding-left:${Math.min(6, r.depth) * 14}px"` : "";
      return `<div class="row"${indent}${r.title ? ` title="${h(r.title)}"` : ""}>${r.state ? dot(r.state) : ""}${label}<span class="val">${h(r.value == null ? "" : r.value)}</span>${actionButtons(r.actions)}</div>`;
    }).join("");
  }
  function meters(list) {
    return (list || []).map((m) => `<div class="meter"><div class="line"><span>${h(m.label)}</span><span>${h(m.text || (m.pct != null ? Math.round(m.pct) + "%" : ""))}${m.actions ? " " + actionButtons(m.actions) : ""}</span></div>
      <div class="track"><i class="${h(m.state || stateOf(m.pct))}" style="width:${Math.max(0, Math.min(100, m.pct || 0))}%"></i></div></div>`).join("");
  }
  function bigs(list) {
    if (!list || !list.length) return "";
    return `<div class="bigs">${list.map((b) => `<div class="big"><div class="n ${b.state ? "t-" + h(b.state) : ""}">${h(b.value == null ? "–" : b.value)}${b.unit ? `<small>${h(b.unit)}</small>` : ""}</div><div class="l">${h(b.label)}</div></div>`).join("")}</div>`;
  }
  function generic(data) {
    if (!data) return `<div class="empty">No data yet.</div>`;
    let out = bigs(data.big) + meters(data.meters) + rows(data.rows);
    if (data.text) out += `<div class="t-dim" style="white-space:pre-wrap">${h(data.text)}</div>`;
    if (data.note) out += `<div class="note">${h(data.note)}</div>`;
    if (data.actions) out += `<div class="btns">${actionButtons(data.actions)}</div>`;
    return out || `<div class="empty">Nothing to show.</div>`;
  }

  // Talking to the server ------------------------------------------------------------------
  async function api(path, body) {
    const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path + (TOKEN && body === undefined ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : ""), opts);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }
  const TOKEN = new URLSearchParams(location.search).get("token") || "";

  async function action(widget, name, params) {
    try {
      const res = await api("/api/action", { widget, action: name, params: params || {} });
      if (res.message) toast(res.message, res.ok === false ? "bad" : "");
      setTimeout(refresh, 400);
      return res;
    } catch (e) {
      toast("Couldn't reach Sightline.", "bad");
      return { ok: false };
    }
  }

  let toastTimer;
  function toast(msg, level) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.className = "toast show " + (level || "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = "toast"), 4200);
  }

  const ctx = { h, attr, num, gb, bytes, ago, dur, dot, stateOf, spark, ring, pie, rows, meters, bigs, generic, actionButtons, action, toast, api,
                recent: (key) => (lastStatus && lastStatus.recent && lastStatus.recent[key]) || [],
                data: (id) => (lastStatus && lastStatus.widgets[id] && lastStatus.widgets[id].data) || null,
                readonly: () => !!(lastStatus && lastStatus.readonly), bridge: () => bridge, config: () => config };

  // Drawing --------------------------------------------------------------------------------
  // Cards sit on a snap grid (web/grid.js). Drag a card by its header to move it, drag its corner to resize,
  // double-click the corner to fit the content. Each kind of layout keeps its own saved arrangement.
  const G = window.SightlineGrid;
  let grid = { u: null, layout: [], key: "", auto: true, sig: "" };
  const natural = {};          // id -> {px, w}: the content's natural height, measured once per card width
  let moving = null;           // the card being dragged or resized
  let saveTimer = null;

  function ensureCard(id) {
    if (cards[id]) return cards[id];
    const m = meta[id] || { title: id, size: "m" };
    const el = document.createElement("section");
    el.className = `card size-${m.size || "m"}`;
    el.dataset.widget = id;
    el.innerHTML = `<div class="card-head" title="Drag to move"><span class="card-title">${h(m.title)}</span><span class="card-sum"></span></div>` +
      `<div class="card-body"></div><div class="resize-handle" title="Drag to resize. Double-click to fit the content."></div>`;
    cards[id] = { el, body: el.querySelector(".card-body"), sum: el.querySelector(".card-sum"), head: el.querySelector(".card-head"), html: "" };
    return cards[id];
  }

  function boardBox() {
    const b = document.getElementById("board"), cs = getComputedStyle(b);
    const pl = parseFloat(cs.paddingLeft), pt = parseFloat(cs.paddingTop);
    return { el: b, pl, pt, w: b.clientWidth - pl - parseFloat(cs.paddingRight), h: b.clientHeight - pt - parseFloat(cs.paddingBottom) };
  }

  function arrange(want, status) {
    const box = boardBox();
    const u = G.units(box.w, box.h, document.body.dataset.layout, config.density);
    const key = G.profileKey(u);
    const saved = (config.grid || {})[key];
    // Only rearrange when something that matters changes, so cards don't shuffle while you look at them.
    const nat = {};
    want.forEach((id) => { if (natural[id]) nat[id] = natural[id].px; });
    const sig = [key, want.join(","), JSON.stringify(saved || null), Object.keys(nat).sort().map((id) => `${id}:${nat[id]}`).join(".")].join("|");
    if (sig === grid.sig && grid.u) return;
    const r = G.arrange(want, { u, metas: meta, saved: config.grid || {}, natural: nat, entries: status.widgets, alerts: status.alerts, preferredOrder: config.order });
    grid = Object.assign(r, { u, sig });
  }

  function position(layout, skip) {
    const box = boardBox();
    let maxR = 0, maxB = 0;
    layout.forEach((b) => {
      const p = G.toPx(b, grid.u);
      maxR = Math.max(maxR, p.left + p.width);
      maxB = Math.max(maxB, p.top + p.height);
      const c = cards[b.id];
      if (!c || b.id === skip) return;
      const st = c.el.style;
      st.left = `${box.pl + p.left}px`; st.top = `${box.pt + p.top}px`; st.width = `${p.width}px`; st.height = `${p.height}px`;
      // A new card appears in place; after that, moves animate.
      if (!c.el.classList.contains("placed")) requestAnimationFrame(() => requestAnimationFrame(() => c.el.classList.add("placed")));
    });
    let sp = box.el.querySelector(".grid-spacer");
    if (!sp) { sp = document.createElement("div"); sp.className = "grid-spacer"; box.el.appendChild(sp); }
    sp.style.left = `${box.pl + maxR}px`;
    sp.style.top = `${box.pt + maxB}px`;
  }

  function placeholder(b) {
    const box = boardBox();
    let ph = box.el.querySelector(".grid-placeholder");
    if (!b) { if (ph) ph.remove(); return; }
    if (!ph) { ph = document.createElement("div"); ph.className = "grid-placeholder"; box.el.appendChild(ph); }
    const p = G.toPx(b, grid.u);
    Object.assign(ph.style, { left: `${box.pl + p.left}px`, top: `${box.pt + p.top}px`, width: `${p.width}px`, height: `${p.height}px` });
  }

  function saveLayout(layout) {
    const map = {};
    layout.forEach((b) => (map[b.id] = { x: b.x, y: b.y, w: b.w, h: b.h }));
    config.grid = Object.assign({}, config.grid || {}, { [grid.key]: map });
    clearTimeout(saveTimer);
    const key = grid.key;
    saveTimer = setTimeout(() => api("/api/config", { changes: { grid: { [key]: map } } }).catch(() => toast("Couldn't save the layout.", "bad")), 300);
  }

  // How tall a card would be to show all of its content, in px.
  function measure(id) {
    const c = cards[id];
    const inner = c && c.body.firstElementChild;
    if (!inner) return 0;
    const cs = getComputedStyle(c.el);
    return Math.ceil(c.head.offsetHeight + parseFloat(getComputedStyle(c.head).marginBottom) + inner.offsetHeight +
                     parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + 2);
  }

  const gridHost = {
    grid: () => grid,
    meta: (id) => meta[id],
    locked: () => !!config.locked || ctx.readonly(),
    preview(layout, id) { moving = id; position(layout, id); placeholder(id ? layout.find((b) => b.id === id) : null); },
    commit(layout) {
      moving = null;
      placeholder(null);
      grid = Object.assign({}, grid, { layout, auto: false });
      saveLayout(layout);
      grid.sig = "";   // recompute from the saved layout on the next draw
      if (lastStatus) draw(lastStatus);
    },
    fit(id) { const px = measure(id); return px ? Math.max(2, Math.round((px + grid.u.gap) / (grid.u.rowH + grid.u.gap))) : 0; },
  };

  function draw(status) {
    lastStatus = status;
    const board = document.getElementById("board");
    const want = status.order.filter((id) => {
      const w = status.widgets[id];
      return w && w.enabled && w.available !== false && (w.data || w.error);
    });
    want.forEach((id) => { const c = ensureCard(id); if (c.el.parentNode !== board) board.appendChild(c.el); });
    Object.keys(cards).forEach((id) => { if (!want.includes(id) && cards[id].el.parentNode) cards[id].el.remove(); });
    if (!moving) {
      arrange(want, status);
      position(grid.layout);
    }
    const byId = {};
    grid.layout.forEach((b) => (byId[b.id] = b));
    // Content: each renderer learns its card's size (ctx.box), so it can lay itself out to fit.
    let remeasure = false;
    want.forEach((id) => {
      const w = status.widgets[id], c = cards[id], d = w.data || {};
      c.el.classList.toggle("error", !!w.error && !w.data);
      const sum = d.summary || "";
      if (c.sum.textContent !== sum) c.sum.textContent = sum;
      const b = byId[id];
      const px = b && grid.u ? G.toPx(b, grid.u) : { width: 300, height: 300 };
      const boxInfo = { w: px.width, h: px.height, cols: b ? b.w : 4, rows: b ? b.h : 6,
                        narrow: px.width < 300, wide: px.width >= 560, short: px.height < 200, tall: px.height >= 420 };
      let html;
      try {
        if (w.error && !w.data) html = `<div>${h(w.error)}</div>`;
        else html = renderers[id] ? renderers[id].render(d, Object.assign({ widget: id, box: boxInfo }, ctx)) : generic(d);
      } catch (e) {
        console.error(id, e);
        html = `<div class="t-bad">This card couldn't draw: ${h(e.message)}</div>`;
      }
      if (html != null && html !== c.html) {
        // Keep scroll positions of the card and anything inside it marked .keep-scroll.
        const keep = [c.body.scrollTop, ...[...c.body.querySelectorAll(".keep-scroll")].map((x) => x.scrollTop)];
        c.body.innerHTML = `<div class="body-in">${html}</div>`;
        c.html = html;
        c.body.scrollTop = keep[0];
        [...c.body.querySelectorAll(".keep-scroll")].forEach((x, i) => (x.scrollTop = keep[i + 1] || 0));
      }
      // Smart default: size each card to its content once, when it first shows (or its width changes),
      // then hold still: cards don't reshuffle because a list got longer.
      if (grid.auto && w.data && !moving && b && (!natural[id] || natural[id].w !== b.w)) {
        const n = measure(id);
        if (n) { natural[id] = { px: n, w: b.w }; remeasure = true; }
      }
    });
    if (remeasure) requestAnimationFrame(() => lastStatus && draw(lastStatus));
    drawNotices(status.alerts || []);
    document.body.classList.toggle("readonly", !!status.readonly);
    document.body.classList.toggle("locked", !!config.locked);
  }

  function drawNotices(alerts) {
    const el = document.getElementById("notices");
    const html = alerts.slice(0, 4).map((a) => `<span class="notice ${a.level === "bad" ? "bad" : ""}" title="${h(a.text)}">${dot(a.level === "bad" ? "bad" : "warn")}${h(a.short || a.text)}</span>`).join("") +
      (alerts.length > 4 ? `<span class="notice">+${alerts.length - 4}</span>` : "");
    if (el.innerHTML !== html) el.innerHTML = html;
  }

  // Buttons inside cards: data-action, with an optional two-click confirm.
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-action]");
    if (!b || b.disabled) return;
    const card = b.closest("[data-widget]");
    const widget = b.dataset.widget || (card && card.dataset.widget);
    if (!widget) return;
    if (ctx.readonly()) return toast("This view is read-only.");
    if (b.hasAttribute("data-confirm") && !b.classList.contains("armed")) {
      b.classList.add("armed");
      b._label = b.innerHTML;
      b.textContent = "Sure?";
      setTimeout(() => { if (b.classList.contains("armed")) { b.classList.remove("armed"); b.innerHTML = b._label; } }, 4000);
      return;
    }
    if (b.classList.contains("armed")) { b.classList.remove("armed"); b.innerHTML = b._label; }
    b.disabled = true;
    let params = {};
    try { params = JSON.parse(b.dataset.params || "{}"); } catch (e) { /* none */ }
    await action(widget, b.dataset.action, params);
    b.disabled = false;
  });

  async function refresh() {
    try {
      draw(await api("/api/status"));
    } catch (e) {
      document.getElementById("notices").innerHTML = `<span class="notice bad">Can't reach the Sightline server</span>`;
    }
  }

  async function loadMeta() {
    const res = await api("/api/widgets");
    meta = {};
    res.widgets.forEach((w) => (meta[w.id] = w));
    Object.values(cards).forEach((c) => { const m = meta[c.el.dataset.widget]; if (m) c.el.className = `card size-${m.size || "m"}`; });
    return res.widgets;
  }

  async function loadConfig() {
    config = await api("/api/config");
    applyLook();
    return config;
  }

  function applyLook() {
    document.body.dataset.layout = config.layout || "window";
    document.body.dataset.theme = config.theme || "dark";
    document.body.dataset.density = config.density || "comfortable";
    // Phones and narrow windows: one column, whatever the setting.
    if (window.innerWidth < 600) document.body.dataset.layout = "column";
    else if (window.innerWidth < 700 && config.layout === "strip") document.body.dataset.layout = "window";
  }

  async function loadRenderers() {
    const res = await api("/api/renderers");
    const load = (name, retry) => new Promise((ok) => {
      const s = document.createElement("script");
      s.src = `/static/widgets/${name}.js${retry ? "?r=1" : ""}`;
      s.onload = ok;
      s.onerror = () => { s.remove(); retry ? ok() : setTimeout(() => load(name, true).then(ok), 500); };
      document.head.appendChild(s);
    });
    await Promise.all(res.renderers.map((name) => load(name, false)));
  }

  window.Sightline = {
    register(id, r) { renderers[id] = r; },
    ctx, refresh, loadMeta, loadConfig, applyLook, toast,
    resetLayout() {   // forget this layout's arrangement and go back to the smart default
      const key = grid.key;
      if (config.grid) config.grid[key] = null;
      Object.keys(natural).forEach((k) => delete natural[k]);
      grid.sig = "";
      return api("/api/config", { changes: { grid: { [key]: null } } }).then(() => lastStatus && draw(lastStatus));
    },
    layoutKey: () => grid.key,
    meta: () => meta, status: () => lastStatus,
    async saveConfig(changes) {
      const res = await api("/api/config", { changes });
      config = res.config;
      applyLook();
      if (bridge) bridge.postMessage({ config: { layout: config.layout, screen: config.screen, strip_height: config.strip_height, column_width: config.column_width } });
      setTimeout(refresh, 300);
      return config;
    },
  };

  async function start() {
    await loadConfig().catch(() => {});
    await loadRenderers().catch(() => {});
    await loadMeta().catch(() => {});
    G.attach(document.getElementById("board"), gridHost);
    await refresh();
    setInterval(refresh, 3000);
    setInterval(() => loadMeta().catch(() => {}), 60000);
    if (config && !config.setup_done && !TOKEN && window.SightlineSettings) window.SightlineSettings.setup();
  }
  window.addEventListener("resize", () => { applyLook(); if (lastStatus && !moving) draw(lastStatus); });
  document.addEventListener("DOMContentLoaded", () => {
    // Tell the Mac app when Settings or a card is open, so a strip or column can float above other windows.
    const ov = document.getElementById("overlay");
    if (bridge) new MutationObserver(() => bridge.postMessage({ overlay: !ov.hidden })).observe(ov, { attributes: true, attributeFilter: ["hidden"] });
    start();
  });
})();
