(function () {
  let filter = "";       // the search box, kept across redraws
  let open = null;       // path of the model whose details are showing
  let typing = false;    // the search box had focus when the card last redrew
  let caret = 0;

  const card = () => document.querySelector('[data-widget="ai_library"]');

  function applyFilter(root) {
    const q = filter.trim().toLowerCase();
    (root || document).querySelectorAll("[data-libsearch]").forEach((r) => {
      r.style.display = !q || r.dataset.libsearch.includes(q) ? "" : "none";
    });
  }

  document.addEventListener("input", (ev) => {
    if (!ev.target.classList || !ev.target.classList.contains("lib-search")) return;
    filter = ev.target.value;
    caret = ev.target.selectionStart || filter.length;
    applyFilter(card());
  });
  document.addEventListener("focusin", (ev) => { if (ev.target.classList && ev.target.classList.contains("lib-search")) typing = true; });
  document.addEventListener("focusout", (ev) => {
    if (!ev.target.classList || !ev.target.classList.contains("lib-search")) return;
    // A redraw replaces the box; only stop typing if focus really went somewhere else.
    setTimeout(() => { const a = document.activeElement; typing = !!(a && a.classList && a.classList.contains("lib-search")); }, 0);
  });
  // After a redraw, put the cursor back in the search box if it was there.
  new MutationObserver(() => {
    const c = card();
    const box = c && c.querySelector(".lib-search");
    if (!box || !typing || document.activeElement === box) return;
    box.focus();
    try { box.setSelectionRange(caret, caret); } catch (e) { /* not a text box */ }
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener("click", (ev) => {
    const r = ev.target.closest("[data-libmodel]");
    if (!r || ev.target.closest("[data-action]")) return;
    open = open === r.dataset.libmodel ? null : r.dataset.libmodel;
    Sightline.refresh();
  });

  const tokens = (n) => (n >= 1048576 ? `${+(n / 1048576).toFixed(1)}M` : n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));
  const size = (c, gb) => (gb >= 100 ? `${c.num(gb, 0)} GB` : gb >= 1 ? `${c.num(gb, 1)} GB` : `${c.num(gb * 1024, 0)} MB`);

  function details(m, d, c) {
    const lines = [
      `<div class="row" style="padding:1px 0"><span class="lbl t-faint">Where</span><span class="val" style="white-space:normal;word-break:break-all;text-align:right">${c.h(m.path)}</span></div>`,
      `<div class="row" style="padding:1px 0"><span class="lbl t-faint">Format</span><span class="val">${c.h([m.format, m.quant].filter(Boolean).join(" · ") || "–")}</span></div>`,
      `<div class="row" style="padding:1px 0"><span class="lbl t-faint">Memory to run it</span><span class="val">about ${c.num(m.need_gb, 1)} GB with 8K tokens of context</span></div>`,
      `<div class="row" style="padding:1px 0"><span class="lbl t-faint">Last changed</span><span class="val">${c.ago(m.modified)}</span></div>`,
    ];
    if (m.partial) lines.push(`<div class="t-warn">${m.partial} file${m.partial > 1 ? "s" : ""} didn't finish downloading.</div>`);
    let ctxTable = "";
    if (m.context && m.context.length) {
      ctxTable = `<div class="sep">Extra memory for context</div><table class="grid" style="width:100%"><tr><th style="text-align:left">Tokens</th><th style="text-align:right">Extra memory</th><th style="text-align:right">Total</th></tr>` +
        m.context.map((x) => `<tr><td>${tokens(x.tokens)}</td><td style="text-align:right">${c.num(x.gb, x.gb < 10 ? 1 : 0)} GB</td><td style="text-align:right">${c.num(m.gb * 1.1 + x.gb, 0)} GB</td></tr>`).join("") +
        `</table>${m.max_tokens ? `<div class="note">Longest context it takes: ${tokens(m.max_tokens)} tokens.</div>` : ""}`;
    }
    let copies = "";
    if (m.dup != null && d.duplicates[m.dup]) {
      const g = d.duplicates[m.dup];
      copies = `<div class="sep">Copies (${g.members.length}) · ${c.num(g.reclaim_gb, 1)} GB could be freed</div>` +
        g.members.map((i) => d.models[i]).filter(Boolean).map((x) => {
          const btn = x.can_trash && !c.readonly()
            ? `<button class="btn quiet" data-action="trash" data-params="${c.attr({ path: x.path })}" data-confirm title="Moves this copy to the Trash. You can put it back from there.">Move to Trash</button>` : "";
          const frees = x.frees_gb < x.gb * 0.5 ? ` · frees ${size(c, x.frees_gb)} (its files are shared)` : "";
          return `<div class="row" style="padding:1px 0"><span class="stack"><span class="lbl">${c.h(x.store)} · ${c.h(x.drive)}${x.path === m.path ? " (this one)" : ""}</span>
            <span class="sub" title="${c.h(x.path)}">${c.h(x.path)}</span></span><span class="val">${size(c, x.gb)}${frees}</span>${btn}</div>`;
        }).join("");
    }
    return `<div style="padding:2px 0 8px 10px;font-size:11px;border-left:2px solid var(--line-2);margin:0 0 4px 4px">${lines.join("")}${ctxTable}${copies}</div>`;
  }

  Sightline.register("ai_library", {
    render(d, c) {
      if (!d.models || (!d.models.length && d.scanning)) return `<div class="empty">Looking for models on this Mac and its drives…</div>`;
      if (!d.models.length) return `<div class="empty">No models found.</div>`;
      const head = c.bigs([
        { label: "models", value: d.count },
        { label: "on disk", value: d.total_gb >= 100 ? String(Math.floor(d.total_gb)) : c.num(d.total_gb, 1), unit: "GB" },
        { label: d.duplicates.length ? `in ${d.duplicates.length} duplicate${d.duplicates.length > 1 ? "s" : ""}` : "no duplicates",
          value: c.num(d.reclaim_gb, d.reclaim_gb >= 100 ? 0 : 1), unit: "GB", state: d.reclaim_gb > 1 ? "warn" : "" },
      ]);
      const chips = [
        `<span class="chip" title="Memory free right now, minus ${c.num(d.headroom_gb, 0)} GB kept for everything else">${c.num(d.available_gb, 0)} GB free for a model</span>`,
        `<span class="chip" title="The most memory the graphics chip may use">GPU limit ${c.num(d.ceiling_gb, 0)} GB</span>`,
        `<span class="chip ok">${d.fit_count} fit now</span>`,
      ];
      const status = d.scanning ? "Looking again…" : `Checked ${c.ago(d.scanned_at)} in ${c.num(d.scan_took, 1)} s`;
      const list = d.models.map((m) => {
        const search = [m.name, m.store, m.drive, m.format, m.quant, m.verdict].join(" ").toLowerCase();
        const sub = [m.store === "Hugging Face" || m.store === "LM Studio" || m.store === "Ollama" ? `${m.store} · ${m.drive}` : m.store, m.format, m.quant].filter(Boolean).join(" · ");
        const dupTag = m.dup != null ? ` <span class="chip warn" title="There's another copy of this model">copy</span>` : "";
        return `<div data-libsearch="${c.h(search)}"><div class="row clickable" data-libmodel="${c.h(m.path)}" title="${c.h(m.path)}">
            <span class="stack"><span class="lbl">${m.loaded ? c.dot("ok") + " " : ""}${c.h(m.name)}${dupTag}</span><span class="sub">${c.h(sub)}</span></span>
            <span class="val">${size(c, m.gb)}</span><span class="chip ${c.h(m.state)}" style="flex:none;max-width:45%;overflow:hidden;text-overflow:ellipsis;display:inline-block" title="${c.h(m.verdict)}. Needs about ${c.num(m.need_gb, 0)} GB.">${c.h(m.verdict)}</span></div>
          ${open === m.path ? details(m, d, c) : ""}</div>`;
      }).join("");
      const html = `${head}<div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0">${chips.join("")}</div>
        <div style="display:flex;gap:6px;align-items:center;margin:4px 0 6px">
          <input class="lib-search" type="search" placeholder="Filter by name, place or format" value="${c.h(filter)}"
            style="flex:1;min-width:0;font:inherit;font-size:12px;padding:4px 8px;border-radius:7px;border:1px solid var(--line-2);background:var(--card-2);color:var(--text)">
          <button class="btn quiet" data-action="rescan" title="Look through every folder and drive again">Look again</button></div>
        <div class="keep-scroll" style="max-height:440px;overflow:auto">${list}</div>
        <div class="note">${c.h(status)}${d.note ? " · " + c.h(d.note) : ""}</div>`;
      // Rows the filter hides are drawn hidden, so a redraw keeps the filter.
      const q = filter.trim().toLowerCase();
      return q ? html.replace(/<div data-libsearch="([^"]*)">/g, (all, s) => (s.includes(c.h(q)) ? all : `<div data-libsearch="${s}" style="display:none">`)) : html;
    },
  });
})();
