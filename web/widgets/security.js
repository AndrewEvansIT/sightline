(function () {
  let showAll = false; // list every login item, not just new ones

  document.addEventListener("click", (ev) => {
    const o = ev.target.closest("[data-sec-open]");
    if (o) {
      const c = Sightline.ctx;
      if (c.readonly()) return c.toast("This view is read-only.");
      c.api("/api/open", { target: o.dataset.secOpen }).then((r) => { if (r && r.ok === false && r.message) c.toast(r.message, "bad"); })
        .catch(() => c.toast("Couldn't open System Settings.", "bad"));
      return;
    }
    if (ev.target.closest("[data-sec-toggle]")) {
      showAll = !showAll;
      Sightline.refresh();
    }
  });

  Sightline.register("security", {
    render(d, c) {
      const ro = c.readonly();
      const open = (target, label) => (ro ? "" : `<button class="btn quiet" data-sec-open="${c.h(target)}" title="Open this in System Settings">${c.h(label || "Open")}</button>`);
      const onOff = (v, onText, offText) => (v === true ? onText : v === false ? offText : "can't tell");
      const st = (v) => (v === true ? "ok" : v === false ? "bad" : "off");
      const fw = d.firewall || {};
      const row = (label, state, value, sub, btn, title) => `<div class="row"${title ? ` title="${c.h(title)}"` : ""}>${c.dot(state)}
          ${sub ? `<span class="stack"><span class="lbl">${c.h(label)}</span><span class="sub">${c.h(sub)}</span></span>` : `<span class="lbl">${c.h(label)}</span>`}
          <span class="val">${c.h(value)}</span>${btn || ""}</div>`;
      let out = "";
      out += row("Firewall", fw.on === false ? "warn" : st(fw.on), onOff(fw.on, "On", "Off"),
        fw.on ? (fw.stealth ? "Stealth mode on: ignores pings from strangers" : "Stealth mode off") : "Blocks apps from accepting connections you didn't allow",
        open("firewall"), "Controls which apps can accept connections from other computers.");
      out += row("FileVault", st(d.filevault), onOff(d.filevault, "On", "Off"), "Encrypts your disk, so a lost Mac can't be read", open("privacy"));
      out += row("Gatekeeper", st(d.gatekeeper), onOff(d.gatekeeper, "On", "Off"), "Only opens apps that Apple has checked", open("privacy"));
      out += row("System Integrity Protection", st(d.sip), onOff(d.sip, "On", "Off"), "Stops anything, even admin tools, from changing macOS itself", "");

      // Updates
      const u = d.updates || {};
      const waiting = u.waiting || [];
      const checked = u.checked ? `Checked ${c.ago(u.checked)}` : d.updates_running ? "Checking now…" : "Not checked yet";
      out += `<div class="sep">Updates</div>`;
      if (d.auto_updates_text) out += row("Automatic updates", d.auto_updates_state || "off", "", d.auto_updates_text, open("updates"));
      out += row(waiting.length ? `${waiting.length} update${waiting.length === 1 ? "" : "s"} waiting` : u.pending ? "Updates" : "Up to date",
        waiting.length ? (u.restart ? "warn" : "ok") : u.pending ? "off" : "ok",
        u.restart ? "needs a restart" : "", waiting.length ? waiting.join(", ") : checked,
        waiting.length ? open("updates") : (!ro && !d.updates_running ? `<button class="btn quiet" data-action="check_updates">Check</button>` : ""),
        waiting.length ? checked : "");

      // Login items
      const items = d.login_items || [];
      const fresh = items.filter((i) => i.new);
      const shown = showAll ? items : fresh;
      out += `<div class="sep">Starts automatically</div>`;
      out += shown.map((i) => {
        const sub = [i.where, i.program ? i.program.split("/").pop() : "", i.missing ? "program is gone (leftover)" : "", i.disabled ? "switched off" : ""].filter(Boolean).join(" · ");
        const btns = (i.new && !ro ? `<button class="btn quiet" data-action="accept" data-params="${c.attr({ path: i.path })}" title="You know this one: stop flagging it">Accept</button>` : "") + open("login-items", "Settings");
        return row(i.label, i.new ? "warn" : i.missing ? "off" : "ok", i.new ? "new" : "", sub, btns, i.command || i.path);
      }).join("");
      out += `<div class="row clickable" data-sec-toggle><span class="lbl t-faint">${showAll ? "Show only new ones" : fresh.length ? `Show all ${items.length}` : `Nothing new · show all ${items.length}`}</span></div>`;
      return out;
    },
  });
})();
