(function () {
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("[data-devport]");
    if (!a) return;
    ev.preventDefault();
    window.open(`http://localhost:${a.dataset.devport}`, "_blank");
  });

  Sightline.register("devservers", {
    render(d, c) {
      const servers = d.servers || [];
      if (!servers.length) return `<div class="empty">No dev servers running.</div>`;
      return servers.map((s) => {
        const name = s.project ? `${s.tool} — ${s.project}` : s.tool;
        const ports = (s.ports || []).map((p) => `<a href="#" class="chip" style="text-decoration:none;color:var(--dim)" data-devport="${Number(p)}" title="Open http://localhost:${Number(p)} in your browser">:${Number(p)}</a>`).join(" ");
        const sub = [s.secs != null ? `running ${c.dur(s.secs)}` : "", s.mem ? c.bytes(s.mem) : "", s.workers ? `${s.workers} worker${s.workers === 1 ? "" : "s"}` : ""]
          .filter(Boolean).join(" · ");
        const stop = c.readonly() ? "" : `<button class="btn quiet" data-action="stop" data-params="${c.attr({ pid: s.pid })}" data-confirm title="Ask this server and its workers to close">Stop</button>`;
        const open = s.ports && s.ports.length ? `<button class="btn" data-devport="${Number(s.ports[0])}" title="Open http://localhost:${Number(s.ports[0])} in your browser">Open</button>` : "";
        return `<div class="row" title="${c.h(s.folder || "")}"><span class="stack"><span class="lbl">${c.h(name)}</span><span class="sub">${c.h(sub)}</span></span>
          <span class="val" style="display:flex;gap:4px">${ports}</span>${open}${stop}</div>`;
      }).join("");
    },
  });
})();
