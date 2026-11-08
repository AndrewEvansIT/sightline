/* Settings window and first-run setup. Everything saves as you change it. */
(function () {
  "use strict";
  const S = () => window.Sightline;
  const c = () => window.Sightline.ctx;
  const overlay = () => document.getElementById("overlay");
  const app = () => window.SightlineApp || null;   // set by the Mac app; absent in a browser
  const bridge = () => window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sightline;
  const CATS = { system: "System", ai: "Local AI", health: "Mac health", network: "Network", security: "Security", developer: "Developer", custom: "Your own" };
  let tab = "layout";
  let widgets = [];
  let conf = {};

  function close() { overlay().hidden = true; overlay().innerHTML = ""; }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || overlay().hidden) return;
    const setupClose = document.getElementById("su-close");
    if (setupClose) setupClose.click(); else close();
  });

  function sw(id, on, extra = "") {
    return `<label class="switch"><input type="checkbox" id="${id}" ${on ? "checked" : ""} ${extra}><span></span></label>`;
  }

  async function open(which) {
    tab = which || tab;
    [conf, widgets] = await Promise.all([S().loadConfig(), S().loadMeta()]);
    overlay().hidden = false;
    overlay().innerHTML = `<div class="sheet" role="dialog" aria-label="Settings">
      <div class="sheet-head"><h2>Settings</h2><button class="btn quiet x" id="set-close">Done</button></div>
      <div class="sheet-main"><nav class="tabs" id="set-tabs">
        ${[["layout", "Layout"], ["widgets", "Widgets"], ["alerts", "Alerts"], ["history", "History"], ["tiles", "Script tiles"], ["sharing", "Phone view"], ["profiles", "Profiles"], ["about", "About"]]
          .map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}
      </nav><div class="pane" id="set-pane"></div></div></div>`;
    overlay().onclick = (e) => { if (e.target === overlay()) close(); };
    document.getElementById("set-close").onclick = close;
    document.getElementById("set-tabs").onclick = (e) => {
      const b = e.target.closest("[data-tab]");
      if (!b) return;
      tab = b.dataset.tab;
      [...document.querySelectorAll("#set-tabs button")].forEach((x) => x.classList.toggle("on", x === b));
      draw();
    };
    draw();
  }

  function draw() {
    const old = document.getElementById("set-pane");
    if (!old) return;
    const pane = old.cloneNode(false);   // a fresh element, so listeners from the last tab don't pile up
    old.replaceWith(pane);
    ({ layout, widgets: widgetsPane, alerts, history, tiles, sharing, profiles, about })[tab](pane);
  }

  async function save(changes) {
    conf = await S().saveConfig(changes);
    return conf;
  }

  // Layout ---------------------------------------------------------------------------------
  function layout(pane) {
    const a = app();
    const screens = a && a.screens ? a.screens : [];
    pane.innerHTML = `<h3>Where it sits</h3>
      <div class="choice" id="lay-choice">
        ${[["strip", "Bottom strip", "Along the bottom of a screen, under your windows"], ["column", "Side column", "A tall column at the right edge"], ["window", "Window", "A normal window you can move and resize"]]
          .map(([k, l, s]) => `<button data-layout="${k}" class="${conf.layout === k ? "on" : ""}">${l}<small>${s}</small></button>`).join("")}
      </div>
      ${a ? `<div class="field"><div class="f-l">Screen<small>Auto picks a portrait screen if you have one, else the main screen.</small></div>
        <select id="lay-screen"><option value="auto">Auto</option>${screens.map((s) => `<option value="${c().h(s.id)}" ${conf.screen === s.id ? "selected" : ""}>${c().h(s.name)} (${s.w}×${s.h})</option>`).join("")}</select></div>
      <div class="field"><div class="f-l">Strip height<small>Points tall when it sits along the bottom.</small></div><input type="number" id="lay-height" min="240" max="1200" step="10" value="${conf.strip_height}"></div>
      <div class="field"><div class="f-l">Column width</div><input type="number" id="lay-width" min="300" max="900" step="10" value="${conf.column_width}"></div>
      <div class="field"><div class="f-l">Open at login<small>Starts Sightline when you log in.</small></div>${sw("lay-login", a.loginItem)}</div>
      <div class="field"><div class="f-l">Menu bar<small>A small readout in the menu bar: processor, memory and models.</small></div>${sw("lay-menubar", a.menuBar !== false)}</div>`
        : `<p class="note">Screen, size and login options appear when Sightline runs as the Mac app. In a browser, the layout follows the window.</p>`}
      <h3>Look</h3>
      <div class="field"><div class="f-l">Theme</div><select id="lay-theme">${[["dark", "Dark"], ["light", "Light"], ["auto", "Match macOS"]].map(([k, l]) => `<option value="${k}" ${conf.theme === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><div class="f-l">Density</div><select id="lay-density">${[["comfortable", "Comfortable"], ["compact", "Compact"]].map(([k, l]) => `<option value="${k}" ${conf.density === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`;
    pane.querySelector("#lay-choice").onclick = async (e) => {
      const b = e.target.closest("[data-layout]");
      if (!b) return;
      await save({ layout: b.dataset.layout });
      layout(pane);
    };
    const on = (id, ev, fn) => { const el = pane.querySelector(id); if (el) el.addEventListener(ev, fn); };
    on("#lay-screen", "change", (e) => save({ screen: e.target.value }));
    on("#lay-height", "change", (e) => save({ strip_height: Number(e.target.value) }));
    on("#lay-width", "change", (e) => save({ column_width: Number(e.target.value) }));
    on("#lay-theme", "change", (e) => save({ theme: e.target.value }));
    on("#lay-density", "change", (e) => save({ density: e.target.value }));
    on("#lay-login", "change", (e) => bridge() && bridge().postMessage({ loginItem: e.target.checked }));
    on("#lay-menubar", "change", (e) => bridge() && bridge().postMessage({ menuBar: e.target.checked }));
  }

  // Widgets ------------------------------------------------------------------------------
  let expanded = null;
  function field(w, s) {
    const v = w.values && s.key in w.values ? w.values[s.key] : s.default;
    const id = `wf-${w.id}-${s.key}`;
    let input;
    if (s.type === "bool") input = sw(id, v, `data-w="${w.id}" data-k="${s.key}" data-t="bool"`);
    else if (s.type === "int") input = `<input type="number" id="${id}" value="${c().h(v)}" data-w="${w.id}" data-k="${s.key}" data-t="int">`;
    else if (s.type === "choice") input = `<select id="${id}" data-w="${w.id}" data-k="${s.key}" data-t="text">${(s.choices || []).map((ch) => { const [val, lab] = Array.isArray(ch) ? ch : [ch, ch]; return `<option value="${c().h(val)}" ${val === v ? "selected" : ""}>${c().h(lab)}</option>`; }).join("")}</select>`;
    else if (s.type === "list") input = `<input type="text" id="${id}" value="${c().h((v || []).join(", "))}" placeholder="comma separated" data-w="${w.id}" data-k="${s.key}" data-t="list">`;
    else input = `<input type="text" id="${id}" value="${c().h(v == null ? "" : v)}" data-w="${w.id}" data-k="${s.key}" data-t="text">`;
    return `<div class="field"><div class="f-l">${c().h(s.label)}${s.help ? `<small>${c().h(s.help)}</small>` : ""}</div>${input}</div>`;
  }

  function widgetsPane(pane) {
    pane.innerHTML = `<p class="note" style="margin:0 0 10px">Drag to reorder. Cards hide on their own when this Mac has nothing to show for them.</p>
      <div class="wlist" id="wlist">${widgets.map((w) => `
        <div class="w" draggable="true" data-id="${w.id}">
          <div class="w-head"><span class="grip" title="Drag to move">⋮⋮</span>
            <div class="w-name">${c().h(w.title)} <span class="t-faint" style="font-size:11px">· ${c().h(CATS[w.category] || w.category)}</span>
              <small>${c().h(w.description)}${w.available === false ? " · Not on this Mac right now" : ""}</small></div>
            ${w.settings && w.settings.length ? `<button class="btn quiet" data-expand="${w.id}">${expanded === w.id ? "Close" : "Options"}</button>` : ""}
            ${sw(`wen-${w.id}`, w.enabled, `data-enable="${w.id}"`)}
          </div>
          ${expanded === w.id ? `<div class="w-body">${w.settings.map((s) => field(w, s)).join("")}</div>` : ""}
        </div>`).join("")}</div>`;
    const list = pane.querySelector("#wlist");
    list.addEventListener("change", async (e) => {
      const t = e.target;
      if (t.dataset.enable) {
        await save({ widgets: { [t.dataset.enable]: { enabled: t.checked } } });
      } else if (t.dataset.w) {
        let v = t.type === "checkbox" ? t.checked : t.value;
        if (t.dataset.t === "int") v = Number(v);
        if (t.dataset.t === "list") v = String(v).split(",").map((x) => x.trim()).filter(Boolean);
        await save({ widgets: { [t.dataset.w]: { [t.dataset.k]: v } } });
      }
      widgets = await S().loadMeta();
    });
    list.addEventListener("click", (e) => {
      const b = e.target.closest("[data-expand]");
      if (!b) return;
      expanded = expanded === b.dataset.expand ? null : b.dataset.expand;
      draw();
    });
    // Drag to reorder.
    let dragging = null;
    list.addEventListener("dragstart", (e) => { dragging = e.target.closest(".w"); if (dragging) { dragging.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; } });
    list.addEventListener("dragend", async () => {
      if (!dragging) return;
      dragging.classList.remove("dragging");
      dragging = null;
      const order = [...list.children].map((x) => x.dataset.id);
      await save({ order });
      widgets = await S().loadMeta();
    });
    list.addEventListener("dragover", (e) => {
      e.preventDefault();
      const over = e.target.closest(".w");
      if (!over || !dragging || over === dragging) return;
      const r = over.getBoundingClientRect();
      list.insertBefore(dragging, e.clientY > r.top + r.height / 2 ? over.nextSibling : over);
    });
    list.addEventListener("drop", (e) => e.preventDefault());
  }

  // Alerts ---------------------------------------------------------------------------------
  function alerts(pane) {
    const rules = (conf.alerts && conf.alerts.rules) || {};
    const groups = widgets.filter((w) => w.alert_rules && w.alert_rules.length);
    pane.innerHTML = `<div class="field"><div class="f-l">Notifications<small>A normal Mac notification when something needs a look. Each one is sent once, then not again for half an hour.</small></div>
        ${sw("al-all", !(conf.alerts && conf.alerts.enabled === false))}</div>
      ${groups.map((w) => `<h3>${c().h(w.title)}</h3>${w.alert_rules.map((r) => `<div class="field"><div class="f-l">${c().h(r.label)}</div>${sw(`al-${r.key}`, rules[r.key] !== false, `data-rule="${c().h(r.key)}"`)}</div>`).join("")}`).join("")}`;
    pane.addEventListener("change", async (e) => {
      if (e.target.id === "al-all") await save({ alerts: { enabled: e.target.checked } });
      else if (e.target.dataset.rule) await save({ alerts: { rules: { [e.target.dataset.rule]: e.target.checked } } });
    });
  }

  // History --------------------------------------------------------------------------------
  const NICE = { "system.cpu": "Processor %", "system.gpu": "Graphics %", "system.temp": "Chip temperature °C", "memory.used_gb": "Memory used (GB)",
                 "memory.swap_gb": "Swap (GB)", "network.ping": "Internet latency (ms)", "power.total_w": "Power (W)" };
  let hKey = "system.cpu", hRange = 86400;
  async function history(pane) {
    const keys = (await c().api("/api/history/keys")).keys;
    if (!keys.includes(hKey) && keys.length) hKey = keys[0];
    pane.innerHTML = `<div class="field"><div class="f-l">Show</div><select id="h-key">${keys.map((k) => `<option value="${c().h(k)}" ${k === hKey ? "selected" : ""}>${c().h(NICE[k] || k)}</option>`).join("")}</select>
      <select id="h-range">${[[3600, "Last hour"], [86400, "Last day"], [604800, "Last week"], [2592000, "Last 30 days"]].map(([v, l]) => `<option value="${v}" ${v === hRange ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div id="h-chart" style="margin-top:12px"></div><p class="note">Kept on this Mac for ${conf.history_days} days, one point a minute.</p>`;
    const chart = async () => {
      const res = await c().api(`/api/history?key=${encodeURIComponent(hKey)}&range=${hRange}`);
      pane.querySelector("#h-chart").innerHTML = lineChart(res.points, hRange);
    };
    pane.querySelector("#h-key").onchange = (e) => { hKey = e.target.value; chart(); };
    pane.querySelector("#h-range").onchange = (e) => { hRange = Number(e.target.value); chart(); };
    chart();
  }

  function lineChart(points, range) {
    if (!points || points.length < 2) return `<div class="empty">Not enough history yet. It fills in as Sightline runs.</div>`;
    const W = 640, H = 220, P = 34;
    const t0 = points[0][0], t1 = points[points.length - 1][0];
    const vals = points.map((p) => p[1]);
    const max = Math.max(...vals), min = Math.min(0, ...vals), span = max - min || 1;
    const x = (t) => P + ((t - t0) / (t1 - t0 || 1)) * (W - P - 8), y = (v) => H - 20 - ((v - min) / span) * (H - 36);
    const line = points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
    const fmtT = (t) => { const d = new Date(t * 1000); return range > 86400 ? `${d.getMonth() + 1}/${d.getDate()}` : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
    const ticks = [0, 0.5, 1].map((f) => t0 + f * (t1 - t0));
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
      ${[0, 0.5, 1].map((f) => { const v = min + f * span; return `<line x1="${P}" x2="${W - 8}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${P - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--faint)">${v >= 100 ? Math.round(v) : v.toFixed(1)}</text>`; }).join("")}
      <polyline points="${line}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
      ${ticks.map((t, i) => `<text x="${x(t)}" y="${H - 4}" text-anchor="${["start", "middle", "end"][i]}" font-size="10" fill="var(--faint)">${fmtT(t)}</text>`).join("")}</svg>`;
  }

  // Script tiles -----------------------------------------------------------------------------
  function tiles(pane) {
    const list = conf.custom || [];
    pane.innerHTML = `<p class="note" style="margin:0 0 10px">A script tile runs a command on your Mac, as you, and shows what it prints. Print JSON in Sightline's card shape for a richer look, or plain text. Only add commands you trust.</p>
      <div id="tl">${list.map((t, i) => `<div style="border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px">
        <div class="field" style="border:0"><div class="f-l">Title</div><input type="text" data-i="${i}" data-k="title" value="${c().h(t.title || "")}"></div>
        <div class="field" style="border:0"><div class="f-l">Command</div><input type="text" data-i="${i}" data-k="command" value="${c().h(t.command || "")}" style="font-family:ui-monospace,Menlo,monospace"></div>
        <div class="field" style="border:0"><div class="f-l">Every (seconds)</div><input type="number" min="5" data-i="${i}" data-k="interval" value="${c().h(t.interval || 60)}">
          ${sw(`tl-en-${i}`, t.enabled !== false, `data-i="${i}" data-k="enabled"`)}<button class="btn quiet" data-remove="${i}">Remove</button></div></div>`).join("") || `<div class="empty">No script tiles yet.</div>`}</div>
      <div class="btns"><button class="btn primary" id="tl-add">Add a tile</button><button class="btn quiet" id="tl-example">Add an example</button></div>`;
    const commit = async (next) => { await save({ custom: next }); draw(); };
    pane.querySelector("#tl-add").onclick = () => commit(list.concat([{ title: "New tile", command: "", interval: 60, enabled: false }]));
    pane.querySelector("#tl-example").onclick = () => commit(list.concat([{ title: "Uptime", command: "uptime", interval: 60, enabled: true }]));
    pane.querySelector("#tl").addEventListener("change", (e) => {
      const i = Number(e.target.dataset.i), k = e.target.dataset.k;
      if (isNaN(i) || !k) return;
      const next = list.map((t) => Object.assign({}, t));
      next[i][k] = e.target.type === "checkbox" ? e.target.checked : k === "interval" ? Number(e.target.value) : e.target.value;
      commit(next);
    });
    pane.querySelector("#tl").addEventListener("click", (e) => {
      const b = e.target.closest("[data-remove]");
      if (b) commit(list.filter((_, i) => i !== Number(b.dataset.remove)));
    });
  }

  // Phone view (LAN, read-only) -------------------------------------------------------------
  async function sharing(pane) {
    const info = await c().api("/api/lan");
    pane.innerHTML = `<div class="field"><div class="f-l">Let phones and tablets on my network see the panel<small>Read-only: nothing can be stopped or changed from another device. The link has a secret code in it; anyone on your network with the link can look.</small></div>${sw("lan-on", info.enabled)}</div>
      ${info.url ? `<h3>Open this on your phone</h3><div class="codeline">${c().h(info.url)}</div>` : ""}
      ${info.restart ? `<p class="note t-warn">Restart Sightline to apply this change.${bridge() ? ` <button class="btn" id="lan-restart">Restart now</button>` : ""}</p>` : ""}`;
    pane.querySelector("#lan-on").onchange = async (e) => {
      const res = await c().api("/api/lan", { enabled: e.target.checked });
      S().toast(res.message || "");
      draw();
    };
    const r = pane.querySelector("#lan-restart");
    if (r) r.onclick = () => bridge().postMessage({ restart: true });
  }

  // Profiles ---------------------------------------------------------------------------------
  function profiles(pane) {
    pane.innerHTML = `<p class="note" style="margin:0 0 10px">A profile is your layout, card order, widget options, alert choices and script tiles in one file. Share it, or bring your setup to another Mac. Script tiles from a profile arrive switched off.</p>
      <div class="btns"><button class="btn primary" id="pf-export">Export my profile</button><label class="btn quiet" style="cursor:pointer">Import a profile<input type="file" id="pf-import" accept=".json,application/json" hidden></label></div>`;
    pane.querySelector("#pf-export").onclick = async () => {
      const prof = await c().api("/api/profile");
      const text = JSON.stringify(prof, null, 2);
      if (bridge()) bridge().postMessage({ save: { name: "sightline-profile.json", text } });
      else {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
        a.download = "sightline-profile.json";
        a.click();
      }
    };
    pane.querySelector("#pf-import").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        const prof = JSON.parse(await f.text());
        const res = await c().api("/api/profile", { profile: prof });
        S().toast(res.message, res.ok ? "" : "bad");
        conf = await S().loadConfig();
        widgets = await S().loadMeta();
      } catch (err) {
        S().toast("That file isn't a Sightline profile.", "bad");
      }
    };
  }

  // About -------------------------------------------------------------------------------------
  function about(pane) {
    const st = S().status() || {};
    pane.innerHTML = `<h3>Sightline ${c().h(st.version || "")}</h3>
      <p class="t-dim">A desk panel for Macs that run local AI: what your Mac is running, what it's talking to, and whether it's healthy.</p>
      <p class="note">Everything stays on this Mac. Sightline only looks; it changes something only when you click a button.</p>
      <div class="btns"><button class="btn quiet" data-open="data-folder">Open data folder</button><button class="btn quiet" data-open="cards">Open saved cards</button>
      <button class="btn quiet" id="ab-setup">Run setup again</button>${bridge() ? `<button class="btn quiet" id="ab-quit">Quit Sightline</button>` : ""}</div>`;
    pane.onclick = (e) => {
      const b = e.target.closest("[data-open]");
      if (b) c().api("/api/open", { target: b.dataset.open });
      if (e.target.id === "ab-setup") { close(); setup(true); }
      if (e.target.id === "ab-quit") bridge().postMessage({ quit: true });
    };
  }

  // First run ----------------------------------------------------------------------------------
  async function setup(again) {
    overlay().hidden = false;
    overlay().innerHTML = `<div class="sheet" role="dialog" aria-label="Welcome"><div class="sheet-head"><h2>Welcome to Sightline</h2><button class="btn quiet x" id="su-close">Close</button></div>
      <div class="pane" id="su-pane"><p class="t-dim">Looking at this Mac…</p></div>
      <div class="sheet-foot"><span class="note grow" style="margin:0">You can change all of this later in Settings.</span><button class="btn primary" id="su-go">Start</button></div></div>`;
    // Every way out finishes setup, so it never comes back on its own.
    const finish = async (layoutPick) => { await save(Object.assign({ setup_done: true }, layoutPick ? { layout: layoutPick } : {})); close(); };
    document.getElementById("su-close").onclick = () => finish();
    document.getElementById("su-go").onclick = () => finish();
    overlay().onclick = (e) => { if (e.target === overlay()) finish(); };
    await new Promise((r) => setTimeout(r, again ? 200 : 4000)); // let the widgets take a first look
    [conf, widgets] = await Promise.all([S().loadConfig(), S().loadMeta()]);
    let pick = conf.layout || "strip";
    const pane = document.getElementById("su-pane");
    const found = widgets.filter((w) => w.available !== false);
    const missing = widgets.filter((w) => w.available === false);
    pane.innerHTML = `<p class="t-dim" style="margin-bottom:12px">Here's what Sightline found. Everything is on; switch off anything you don't want. Nothing needs setting up.</p>
      <div class="setup-grid">${found.map((w) => `<div class="setup-item">${sw(`su-${w.id}`, w.enabled, `data-enable="${w.id}"`)}<div>${c().h(w.title)}<small>${c().h(w.description)}</small></div></div>`).join("")}</div>
      ${missing.length ? `<p class="note">Not on this Mac right now (they appear on their own if that changes): ${missing.map((w) => c().h(w.title)).join(", ")}.</p>` : ""}
      ${app() ? `<h3>Where should it sit?</h3><div class="choice" id="su-lay">${[["strip", "Bottom strip", "Along the bottom of a screen"], ["column", "Side column", "At the right edge"], ["window", "Window", "A normal window"]]
        .map(([k, l, s]) => `<button data-layout="${k}" class="${pick === k ? "on" : ""}">${l}<small>${s}</small></button>`).join("")}</div>` : ""}`;
    pane.addEventListener("change", (e) => { if (e.target.dataset.enable) save({ widgets: { [e.target.dataset.enable]: { enabled: e.target.checked } } }); });
    const lay = pane.querySelector("#su-lay");
    if (lay) lay.onclick = (e) => {
      const b = e.target.closest("[data-layout]");
      if (!b) return;
      pick = b.dataset.layout;
      [...lay.children].forEach((x) => x.classList.toggle("on", x === b));
    };
    document.getElementById("su-go").onclick = () => finish(app() ? pick : null);
  }

  window.SightlineSettings = { open, setup, close };
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("btn-settings").onclick = () => open();
  });
})();
