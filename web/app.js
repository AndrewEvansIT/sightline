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
  function ensureCard(id) {
    if (cards[id]) return cards[id];
    const m = meta[id] || { title: id, size: "m" };
    const el = document.createElement("section");
    el.className = `card size-${m.size || "m"}`;
    el.dataset.widget = id;
    el.innerHTML = `<div class="card-head"><span class="card-title">${h(m.title)}</span><span class="card-sum"></span></div><div class="card-body"></div>`;
    cards[id] = { el, body: el.querySelector(".card-body"), sum: el.querySelector(".card-sum"), html: "" };
    return cards[id];
  }

  function draw(status) {
    lastStatus = status;
    const board = document.getElementById("board");
    const want = status.order.filter((id) => {
      const w = status.widgets[id];
      return w && w.enabled && w.available !== false && (w.data || w.error);
    });
    // Order and presence.
    want.forEach((id, i) => {
      const c = ensureCard(id);
      if (board.children[i] !== c.el) board.insertBefore(c.el, board.children[i] || null);
    });
    Object.keys(cards).forEach((id) => { if (!want.includes(id) && cards[id].el.parentNode) cards[id].el.remove(); });
    // Content.
    want.forEach((id) => {
      const w = status.widgets[id], c = cards[id], d = w.data || {};
      c.el.classList.toggle("error", !!w.error && !w.data);
      const sum = d.summary || "";
      if (c.sum.textContent !== sum) c.sum.textContent = sum;
      let html;
      try {
        if (w.error && !w.data) html = `<div>${h(w.error)}</div>`;
        else html = renderers[id] ? renderers[id].render(d, Object.assign({ widget: id }, ctx)) : generic(d);
      } catch (e) {
        console.error(id, e);
        html = `<div class="t-bad">This card couldn't draw: ${h(e.message)}</div>`;
      }
      if (html != null && html !== c.html) {
        // Keep scroll positions of the card and anything inside it marked .keep-scroll.
        const keep = [c.body.scrollTop, ...[...c.body.querySelectorAll(".keep-scroll")].map((x) => x.scrollTop)];
        c.body.innerHTML = html;
        c.html = html;
        c.body.scrollTop = keep[0];
        [...c.body.querySelectorAll(".keep-scroll")].forEach((x, i) => (x.scrollTop = keep[i + 1] || 0));
      }
    });
    drawNotices(status.alerts || []);
    document.body.classList.toggle("readonly", !!status.readonly);
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
    if (window.innerWidth < 700 && config.layout !== "column") document.body.dataset.layout = "window";
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
    await refresh();
    setInterval(refresh, 3000);
    setInterval(() => loadMeta().catch(() => {}), 60000);
    if (config && !config.setup_done && !TOKEN && window.SightlineSettings) window.SightlineSettings.setup();
  }
  window.addEventListener("resize", applyLook);
  document.addEventListener("DOMContentLoaded", () => {
    // Tell the Mac app when Settings or a card is open, so a strip or column can float above other windows.
    const ov = document.getElementById("overlay");
    if (bridge) new MutationObserver(() => bridge.postMessage({ overlay: !ov.hidden })).observe(ov, { attributes: true, attributeFilter: ["hidden"] });
    start();
  });
})();
