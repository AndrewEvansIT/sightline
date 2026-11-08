(function () {
  // Rename: ask for a name, then call the widget's "rename" action.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-dev-rename]");
    if (!b) return;
    const c = Sightline.ctx;
    if (c.readonly()) return c.toast("This view is read-only.");
    const name = window.prompt("Name this device (leave empty to clear the name):", b.dataset.name || "");
    if (name === null) return;
    c.action("devices", "rename", { mac: b.dataset.devRename, name: name.trim() });
  });

  Sightline.register("devices", {
    render(d, c) {
      const list = d.devices || [];
      const ro = c.readonly();
      const octet = (ip) => (ip ? "." + ip.split(".").pop() : "");
      const rows = list.map((x) => {
        const tags = [];
        if (x.new) tags.push(`<span class="chip warn">new</span>`);
        if (x.router && x.name !== "Router") tags.push(`<span class="chip">router</span>`);
        if (x.private) tags.push(`<span class="chip" title="A private Wi-Fi address: phones, tablets and Macs make these up per network and may change them.">private address</span>`);
        const seen = x.online ? "" : ` title="Last seen ${c.h(c.ago(x.last_seen))}"`;
        const btns = [];
        if (!ro && !x.me) {
          if (x.new) btns.push(`<button class="btn quiet" data-action="mark_known" data-params="${c.attr({ mac: x.mac })}" title="Stop flagging this device">Known</button>`);
          btns.push(`<button class="btn quiet" data-dev-rename="${c.h(x.mac)}" data-name="${c.h(x.renamed ? x.name : "")}" title="Give this device a name">Rename</button>`);
          if (!x.online) btns.push(`<button class="btn quiet" data-action="forget" data-params="${c.attr({ mac: x.mac })}" data-confirm title="Remove it from the list">Forget</button>`);
        }
        return `<div class="row"${seen}>${c.dot(x.online ? (x.new ? "warn" : "ok") : "off")}
          <span class="lbl${x.online ? "" : " t-faint"}">${c.h(x.name)}</span>${tags.join("")}
          <span class="val" title="${c.h(x.ip || "")}${x.mac ? " · " + c.h(x.mac) : ""}">${c.h(octet(x.ip))}</span>${btns.join("")}</div>`;
      }).join("");
      const head = `<div class="t-faint" style="font-size:11px;margin-bottom:4px">${c.h(d.subnet || "")}${d.interface ? " on " + c.h(d.interface) : ""}${d.swept ? "" : " · not swept (big network)"}</div>`;
      return `${head}<div class="keep-scroll">${rows || `<div class="empty">No devices found yet.</div>`}</div>
        ${d.note ? `<div class="note">${c.h(d.note)}</div>` : ""}`;
    },
  });
})();
