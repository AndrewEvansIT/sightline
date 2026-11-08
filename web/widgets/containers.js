(function () {
  const shown = {}; // compose project -> showing its stopped containers

  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-ctr-more]");
    if (!t) return;
    shown[t.dataset.ctrMore] = !shown[t.dataset.ctrMore];
    Sightline.refresh();
  });

  function row(ct, c) {
    const up = ct.state === "running";
    const chips = [];
    if (up && ct.health === "unhealthy") chips.push(`<span class="chip bad">unhealthy</span>`);
    if (up && ct.health === "starting") chips.push(`<span class="chip warn">starting</span>`);
    const ports = (ct.ports || []).slice(0, 3).join(", ") + ((ct.ports || []).length > 3 ? " …" : "");
    const sub = [ct.image, ports ? "port " + ports : "", up ? "" : ct.status].filter(Boolean).join(" · ");
    const btns = c.readonly() ? "" : up
      ? `<button class="btn quiet" data-action="restart" data-params="${c.attr({ id: ct.id })}" title="Restart this container">Restart</button>
         <button class="btn quiet" data-action="stop" data-params="${c.attr({ id: ct.id })}" data-confirm title="Stop this container">Stop</button>`
      : `<button class="btn quiet" data-action="start" data-params="${c.attr({ id: ct.id })}" title="Start this container">Start</button>`;
    return `<div class="row"${up ? "" : ' style="opacity:.6"'}>${c.dot(up ? (ct.health === "unhealthy" ? "bad" : "ok") : "off")}
      <span class="stack"><span class="lbl">${c.h(ct.name)}</span><span class="sub">${c.h(sub)}</span></span>
      ${chips.join("")}<span class="val">${up && ct.mem ? c.bytes(ct.mem) : ""}${up && ct.cpu >= 1 ? ` · ${c.num(ct.cpu, 0)}%` : ""}</span>${btns}</div>`;
  }

  Sightline.register("containers", {
    render(d, c) {
      if (!d.running) return `<div class="empty">${c.h(d.note || "Docker isn't running.")}</div>`;
      const all = d.containers || [];
      if (!all.length) return `<div class="empty">No containers.</div>`;
      const groups = {};
      all.forEach((ct) => (groups[ct.project || ""] = groups[ct.project || ""] || []).push(ct));
      const names = Object.keys(groups).sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
      const multi = names.length > 1 || names[0] !== "";
      const body = names.map((p) => {
        const list = groups[p], up = list.filter((x) => x.state === "running"), down = list.filter((x) => x.state !== "running");
        const mem = up.reduce((a, x) => a + (x.mem || 0), 0);
        const head = multi ? `<div class="sep">${c.h(p || "Other containers")} · ${up.length} of ${list.length} running${mem ? " · " + c.bytes(mem) : ""}</div>` : "";
        const more = down.length ? (shown[p] ? down.map((x) => row(x, c)).join("") + `<div class="row clickable t-faint" data-ctr-more="${c.h(p)}" style="font-size:11px">Hide stopped</div>`
          : `<div class="row clickable t-faint" data-ctr-more="${c.h(p)}" style="font-size:11px">${down.length} stopped · show</div>`) : "";
        return head + up.map((x) => row(x, c)).join("") + more;
      }).join("");
      return `${body}<div class="note">${c.h(d.engine || "Docker")}${d.mem ? ` · running containers use ${c.bytes(d.mem)}` : ""}.</div>`;
    },
  });
})();
