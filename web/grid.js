/* Sightline's layout engine: a snap grid for the cards.
 *
 * Every card has a box in grid units: {id, x, y, w, h}. The grid adapts to where the panel sits:
 *   window and column layouts: a fixed number of columns that fit the width; cards fall upward (gravity "v")
 *   strip layout (wide and short): a fixed number of rows that fit the height; cards fall leftward (gravity "h")
 * Layouts are saved per "profile" (layout + grid size), so a strip, a side column and a big window each keep
 * their own arrangement. A profile with no saved arrangement gets a smart default: cards ordered by what
 * matters on this Mac, sized from their content, packed tight. A saved arrangement from a different grid
 * size is adapted rather than thrown away.
 *
 * Pure functions at the top (tested in tests/grid.test.js with Node); pointer handling at the bottom.
 */
(function (root) {
  "use strict";

  const ROW = { comfortable: 40, compact: 32 };          // px per row unit
  const COL = { window: 92, column: 104, strip: 84 };     // target px per column unit
  const PREF = { s: [280, 210], m: [340, 290], l: [560, 340], xl: [760, 430] };  // preferred card size, px
  const MIN_PX = [190, 96];                                // smallest useful card, px

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // The grid for a board of this size. ------------------------------------------------------
  function units(width, height, layout, density) {
    const gap = density === "compact" ? 7 : 10;
    const rowH = ROW[density] || ROW.comfortable;
    if (layout === "strip") {
      const rows = clamp(Math.floor((height + gap) / (rowH + gap)), 3, 40);
      return { layout, mode: "h", rows, cols: Infinity, colW: COL.strip, rowH, gap };
    }
    const target = layout === "column" ? COL.column : COL.window;
    const cols = clamp(Math.round((width + gap) / (target + gap)), 2, 24);
    const colW = Math.max(40, (width - gap * (cols - 1)) / cols);
    return { layout, mode: "v", cols, rows: Infinity, colW, rowH, gap };
  }

  function profileKey(u) {
    return u.mode === "h" ? `strip:${u.rows}` : `${u.layout}:${u.cols}`;
  }

  const span = (px, unit, gap) => Math.max(1, Math.round((px + gap) / (unit + gap)));

  function toPx(b, u) {
    return { left: b.x * (u.colW + u.gap), top: b.y * (u.rowH + u.gap),
             width: b.w * u.colW + (b.w - 1) * u.gap, height: b.h * u.rowH + (b.h - 1) * u.gap };
  }

  // Size in units for a card, from its size hint, the layout, and (if known) its content's natural height.
  function sizeFor(meta, u, naturalPx) {
    const [pw, ph] = PREF[(meta && meta.size) || "m"] || PREF.m;
    const minW = Math.min(span(MIN_PX[0], u.colW, u.gap), u.mode === "v" ? u.cols : 99);
    const minH = span(MIN_PX[1], u.rowH, u.gap);
    let w = span(pw, u.colW, u.gap), h = span(ph, u.rowH, u.gap);
    if (naturalPx) h = clamp(span(naturalPx, u.rowH, u.gap), minH, Math.round(h * 1.5));
    if (u.mode === "v") {
      if (u.layout === "column") w = meta && meta.size === "s" && u.cols >= 4 ? Math.ceil(u.cols / 2) : u.cols;
      w = clamp(w, minW, u.cols);
    } else {
      // Strip: small cards take half the height so two stack; big ones take it all.
      const big = meta && (meta.size === "l" || meta.size === "xl");
      h = big || u.rows < 6 ? u.rows : clamp(Math.max(h, Math.floor(u.rows / 2)), minH, u.rows);
      if (naturalPx && !big && u.rows >= 6) h = clamp(span(naturalPx, u.rowH, u.gap), minH, u.rows);
    }
    return { w, h, minW, minH };
  }

  // What matters most on this Mac goes first. Cards with something wrong move up.
  const BASE = { system: 100, memory: 99, mac_battery: 97, ai_runtimes: 96, gpu_limit: 70, ai_library: 80, ai_speed: 60,
                 ai_downloads: 94, power: 85, storage: 88, links: 62, batteries: 40, health: 66, network: 72, devices: 50,
                 security: 64, connections: 56, agents: 78, repos: 52, devservers: 58, containers: 54, custom: 30 };
  function priority(id, meta, entry, alerts) {
    let p = BASE[id] != null ? BASE[id] : 45;
    const d = (entry && entry.data) || {};
    if (id === "ai_runtimes" && !(d.models && d.models.length)) p -= 30;   // nothing loaded: less urgent
    if (id === "agents" && d.sessions && d.sessions.length) p += 8;
    if ((alerts || []).some((a) => a.widget === id && a.level === "bad")) p += 40;
    else if ((alerts || []).some((a) => a.widget === id)) p += 15;
    return p;
  }

  // Geometry --------------------------------------------------------------------------
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const fits = (b, u) => b.x >= 0 && b.y >= 0 && (u.mode === "h" ? b.y + b.h <= u.rows : b.x + b.w <= u.cols);
  const free = (b, placed) => !placed.some((p) => p.id !== b.id && overlaps(b, p));

  // First free spot, scanning top-to-bottom (v) or left-to-right (h).
  function firstFit(item, placed, u) {
    const b = Object.assign({}, item);
    if (u.mode === "v") {
      b.w = Math.min(b.w, u.cols);
      for (let y = 0; ; y++) for (let x = 0; x + b.w <= u.cols; x++) { b.x = x; b.y = y; if (free(b, placed)) return b; }
    }
    b.h = Math.min(b.h, u.rows);
    for (let x = 0; ; x++) for (let y = 0; y + b.h <= u.rows; y++) { b.x = x; b.y = y; if (free(b, placed)) return b; }
  }

  function pack(items, u) {
    const placed = [];
    items.forEach((it) => placed.push(firstFit(it, placed, u)));
    return placed;
  }

  const order = (u) => (u.mode === "v" ? (a, b) => a.y - b.y || a.x - b.x : (a, b) => a.x - b.x || a.y - b.y);

  // Gravity: slide every card up (v) or left (h) as far as it goes, in reading order.
  function compact(layout, u) {
    const out = [];
    layout.slice().sort(order(u)).forEach((it) => {
      const b = Object.assign({}, it);
      const key = u.mode === "v" ? "y" : "x";
      while (b[key] > 0) { b[key]--; if (!free(b, out)) { b[key]++; break; } }
      out.push(b);
    });
    return out;
  }

  // `moved` goes exactly where asked; anything it lands on is pushed down (v) or right (h); then gravity.
  function place(layout, moved, u) {
    const m = Object.assign({}, moved);
    if (u.mode === "v") { m.w = clamp(m.w, 1, u.cols); m.x = clamp(m.x, 0, u.cols - m.w); m.y = Math.max(0, m.y); }
    else { m.h = clamp(m.h, 1, u.rows); m.y = clamp(m.y, 0, u.rows - m.h); m.x = Math.max(0, m.x); }
    const settled = [m];
    const key = u.mode === "v" ? "y" : "x", size = u.mode === "v" ? "h" : "w";
    layout.filter((b) => b.id !== m.id).sort(order(u)).forEach((it) => {
      const b = Object.assign({}, it);
      let guard = 0;
      while (!free(b, settled) && guard++ < 500) {
        const blockers = settled.filter((p) => overlaps(b, p));
        b[key] = Math.max(...blockers.map((p) => p[key] + p[size]));
      }
      settled.push(b);
    });
    return compact(settled, u);
  }

  // Make any layout valid for this grid: inside the bounds, no overlaps, gravity applied.
  function normalize(layout, u) {
    const placed = [];
    layout.slice().sort(order(u)).forEach((it) => {
      const b = Object.assign({}, it);
      if (u.mode === "v") { b.w = clamp(b.w, 1, u.cols); b.x = clamp(b.x, 0, u.cols - b.w); }
      else { b.h = clamp(b.h, 1, u.rows); b.y = clamp(b.y, 0, u.rows - b.h); }
      let guard = 0;
      while (!free(b, placed) && guard++ < 500) {
        if (u.mode === "v") b.y = Math.max(...placed.filter((p) => overlaps(b, p)).map((p) => p.y + p.h));
        else b.x = Math.max(...placed.filter((p) => overlaps(b, p)).map((p) => p.x + p.w));
      }
      placed.push(b);
    });
    return compact(placed, u);
  }

  // A saved layout made for another grid size, stretched to this one.
  function adapt(saved, fromU, u) {
    const out = [];
    Object.keys(saved || {}).forEach((id) => {
      const s = saved[id];
      if (!s) return;
      const b = { id, x: s.x, y: s.y, w: s.w, h: s.h };
      if (u.mode === "v" && fromU.mode === "v" && fromU.cols !== u.cols) {
        const r = u.cols / fromU.cols;
        b.x = Math.round(s.x * r);
        b.w = Math.max(1, Math.round(s.w * r));
      }
      if (u.mode === "h" && fromU.mode === "h" && fromU.rows !== u.rows) {
        const r = u.rows / fromU.rows;
        b.y = Math.round(s.y * r);
        b.h = Math.max(1, Math.round(s.h * r));
      }
      out.push(b);
    });
    return normalize(out, u);
  }

  // The arrangement to show: saved (or adapted) positions for known cards, first free spot for new ones,
  // or the smart default when nothing is saved for this kind of layout.
  function arrange(ids, ctx) {
    const { u, metas, saved, natural, entries, alerts, preferredOrder } = ctx;
    const want = new Set(ids);
    const key = profileKey(u);
    let base = saved && saved[key];
    let fromU = u;
    if (!base && saved) {
      // Nearest saved profile of the same layout.
      const same = Object.keys(saved).filter((k) => saved[k] && k.split(":")[0] === key.split(":")[0]);
      if (same.length) {
        const n = Number(key.split(":")[1]);
        const best = same.sort((a, b) => Math.abs(Number(a.split(":")[1]) - n) - Math.abs(Number(b.split(":")[1]) - n))[0];
        base = saved[best];
        const v = Number(best.split(":")[1]);
        fromU = u.mode === "v" ? Object.assign({}, u, { cols: v }) : Object.assign({}, u, { rows: v });
      }
    }
    if (base) {
      const kept = adapt(Object.fromEntries(Object.entries(base).filter(([id]) => want.has(id))), fromU, u);
      const known = new Set(kept.map((b) => b.id));
      const fresh = ids.filter((id) => !known.has(id)).map((id) => Object.assign({ id }, sizeFor(metas[id], u, natural && natural[id])));
      let placed = kept.slice();
      fresh.forEach((it) => placed.push(firstFit(it, placed, u)));
      return { layout: placed, auto: false, key };
    }
    const ranked = preferredOrder && preferredOrder.length
      ? ids.slice().sort((a, b) => (preferredOrder.indexOf(a) + 1 || 999) - (preferredOrder.indexOf(b) + 1 || 999))
      : ids.slice().sort((a, b) => priority(b, metas[b], entries && entries[b], alerts) - priority(a, metas[a], entries && entries[a], alerts));
    const items = ranked.map((id) => Object.assign({ id }, sizeFor(metas[id], u, natural && natural[id])));
    return { layout: pack(items, u), auto: true, key };
  }

  const api = { units, profileKey, toPx, sizeFor, priority, overlaps, pack, compact, place, normalize, adapt, arrange, firstFit, PREF };

  // Pointer handling (browser only) ----------------------------------------------------------
  // Drag a card by its header to move it; drag the corner handle to resize; double-click the handle to fit
  // the content. `host` supplies: grid() -> {u, layout}, preview(layout, movingId), commit(layout), fit(id) -> h.
  function attach(board, host) {
    let op = null;
    const MOVE = 4;

    board.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || host.locked()) return;
      const handle = e.target.closest(".resize-handle");
      const head = e.target.closest(".card-head");
      if (!handle && (!head || e.target.closest("button, a, input, select, textarea, label"))) return;
      const card = e.target.closest(".card");
      if (!card) return;
      const g = host.grid();
      const box = g.layout.find((b) => b.id === card.dataset.widget);
      if (!box) return;
      op = { kind: handle ? "resize" : "move", card, id: box.id, start: { x: e.clientX, y: e.clientY }, box: Object.assign({}, box),
             g, px: toPx(box, g.u), active: false, last: null, scroll: { x: board.scrollLeft, y: board.scrollTop }, pointer: e.pointerId };
      e.preventDefault();
    });

    board.addEventListener("pointermove", (e) => {
      if (!op) return;
      const dx = e.clientX - op.start.x + (board.scrollLeft - op.scroll.x), dy = e.clientY - op.start.y + (board.scrollTop - op.scroll.y);
      if (!op.active) {
        if (Math.abs(dx) < MOVE && Math.abs(dy) < MOVE) return;
        op.active = true;
        try { op.card.setPointerCapture(op.pointer); } catch (err) { /* already released */ }
        op.card.classList.add(op.kind === "move" ? "moving" : "resizing");
        document.body.classList.add("grid-busy");
      }
      const u = op.g.u, cw = u.colW + u.gap, rh = u.rowH + u.gap;
      const target = Object.assign({}, op.box);
      if (op.kind === "move") {
        op.card.style.left = `${op.px.left + dx}px`;
        op.card.style.top = `${op.px.top + dy}px`;
        target.x = Math.round((op.px.left + dx) / cw);
        target.y = Math.round((op.px.top + dy) / rh);
      } else {
        const w = Math.max(80, op.px.width + dx), h = Math.max(60, op.px.height + dy);
        op.card.style.width = `${w}px`;
        op.card.style.height = `${h}px`;
        const meta = host.meta(op.id) || {};
        const min = sizeFor(meta, u);
        target.w = Math.max(min.minW, Math.round((w + u.gap) / cw));
        target.h = Math.max(min.minH, Math.round((h + u.gap) / rh));
      }
      const sig = `${target.x},${target.y},${target.w},${target.h}`;
      if (sig !== op.last) {
        op.last = sig;
        op.layout = place(op.g.layout, target, u);
        host.preview(op.layout, op.id);
      }
      autoscroll(e);
    });

    function end(e) {
      if (!op) return;
      const done = op;
      op = null;
      done.card.classList.remove("moving", "resizing");
      document.body.classList.remove("grid-busy");
      if (done.active && done.layout) host.commit(done.layout);
      else host.preview(done.g.layout, null);
    }
    board.addEventListener("pointerup", end);
    board.addEventListener("pointercancel", end);
    board.addEventListener("lostpointercapture", end);

    // Double-click the resize handle: fit the card's height to its content.
    board.addEventListener("dblclick", (e) => {
      const handle = e.target.closest(".resize-handle");
      if (!handle || host.locked()) return;
      const card = handle.closest(".card");
      const g = host.grid();
      const box = g.layout.find((b) => b.id === card.dataset.widget);
      const h = box && host.fit(box.id);
      if (h && h !== box.h) host.commit(place(g.layout, Object.assign({}, box, { h }), g.u));
    });

    function autoscroll(e) {
      const r = board.getBoundingClientRect(), edge = 40, step = 14;
      if (e.clientY > r.bottom - edge) board.scrollTop += step;
      else if (e.clientY < r.top + edge) board.scrollTop -= step;
      if (e.clientX > r.right - edge) board.scrollLeft += step;
      else if (e.clientX < r.left + edge) board.scrollLeft -= step;
    }
  }
  api.attach = attach;

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SightlineGrid = api;
})(typeof window !== "undefined" ? window : globalThis);
