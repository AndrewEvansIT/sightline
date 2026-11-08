(function () {
  let showLocal = false; // also list apps that only listen on this Mac

  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-conn-toggle]")) {
      showLocal = !showLocal;
      Sightline.refresh();
    }
  });

  Sightline.register("connections", {
    render(d, c) {
      const hostChip = (hst) => {
        const tip = [...(hst.names || []), ...(hst.ips || [])].join("\n");
        const label = hst.local ? `${hst.host} · local network` : hst.owner ? hst.owner : hst.host;
        return `<span class="chip${hst.local ? " ok" : ""}" title="${c.h(tip)}">${c.h(label)}${hst.count > 1 ? ` <span class="t-faint">×${hst.count}</span>` : ""}</span>`;
      };
      const talkers = (d.talkers || []).map((a) => {
        const more = a.host_count > a.hosts.length ? `<span class="chip t-faint">+${a.host_count - a.hosts.length} more</span>` : "";
        return `<div style="padding:3px 0">
          <div class="row" style="padding:0"><span class="lbl">${c.h(a.name)}${a.kind === "macos" ? ' <span class="t-faint">· macOS</span>' : ""}</span>
            <span class="val">${a.count} connection${a.count === 1 ? "" : "s"}</span></div>
          <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:2px">${a.hosts.map(hostChip).join("")}${more}</div></div>`;
      }).join("");

      const all = d.listening || [];
      const exposed = all.filter((a) => a.exposed);
      const local = all.filter((a) => !a.exposed);
      const portChip = (p) => `<span class="chip${p.new ? " warn" : ""}" title="${c.h(p.proto)} port ${c.h(p.port)}: ${c.h(p.where)}${p.new ? " (new today)" : ""}">${p.proto === "UDP" ? "udp " : ""}${c.h(p.port)}</span>`;
      const listRow = (a) => {
        const ports = a.ports.filter((p) => p.exposed === a.exposed || !a.exposed);
        const shown = ports.slice(0, 8);
        return `<div style="padding:3px 0">
          <div class="row" style="padding:0">${c.dot(a.exposed ? (a.kind === "macos" ? "ok" : "warn") : "off")}<span class="lbl">${c.h(a.name)}${a.kind === "macos" ? ' <span class="t-faint">· macOS</span>' : ""}</span>
            <span class="val">${a.exposed ? "open to your network" : "this Mac only"}</span></div>
          <div style="display:flex;gap:4px;flex-wrap:wrap;margin:2px 0 0 14px">${shown.map(portChip).join("")}${ports.length > shown.length ? `<span class="chip t-faint">+${ports.length - shown.length} more</span>` : ""}</div></div>`;
      };
      const listening = exposed.map(listRow).join("") + (showLocal ? local.map(listRow).join("") : "") +
        (local.length ? `<div class="row clickable" data-conn-toggle><span class="lbl t-faint">${showLocal ? "Hide" : "Show"} ${local.length} app${local.length === 1 ? "" : "s"} listening on this Mac only</span></div>` : "");

      return `<div class="sep">Talking to the internet${d.internet_count ? ` · ${d.internet_count}` : ""}</div>
        ${talkers || `<div class="empty">No app is connected right now.</div>`}
        <div class="sep">Open to your network</div>
        ${listening || `<div class="empty">Nothing is accepting connections.</div>`}
        <div class="note">${c.h(d.note || "")} Green: part of macOS. Amber: an app accepting connections from other devices on your network.</div>`;
    },
  });
})();
