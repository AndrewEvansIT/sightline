Sightline.register("gpu_limit", {
  render(d, c) {
    const steps = d.steps || [];
    const can = d.installed && !c.readonly();
    const seg = steps.map((s) => {
      const on = s.mb === d.mb;
      const warn = (d.warnings || {})[String(s.mb)];
      const tip = `${s.name}: the GPU may use ${s.gb} GB, leaving ${s.leaves_gb} GB for macOS and apps${warn ? "\n" + warn.join("\n") : ""}`;
      const style = `flex:1;min-width:0;padding:6px 2px;border:0;border-radius:7px;cursor:${can && !on ? "pointer" : "default"};font-size:11px;line-height:1.2;` +
        (on ? "background:var(--accent);color:#0b0c0e;font-weight:600" : "background:transparent;color:var(--dim)");
      const params = warn ? { mb: s.mb, confirm: true } : { mb: s.mb };
      const act = can && !on ? ` data-action="set" data-params="${c.attr(params)}"${warn ? " data-confirm" : ""}` : "";
      return `<button style="${style}" title="${c.h(tip)}"${act}${can || on ? "" : " disabled"}>${c.h(s.name)}<br><span style="opacity:.75">${s.gb} GB</span></button>`;
    }).join("");
    const cur = steps.find((s) => s.mb === d.mb);
    const line = d.gb != null
      ? `The GPU may use <b>${c.h(d.gb)} GB</b> of ${c.h(d.total_gb)} GB for models${cur ? "" : " (set outside Sightline)"}.`
      : "Couldn't read the GPU memory limit.";
    const maxWarn = steps.length ? (d.warnings || {})[String(steps[steps.length - 1].mb)] : null;
    let foot = "";
    if (!d.installed) {
      foot = c.readonly() ? "" : `<div class="btns"><button class="btn primary" data-action="install" title="Installs a small helper that can only set these steps, and re-applies your choice after a restart">Install helper (asks for your password)</button></div>`;
    }
    return `<div style="font-size:12px;margin-bottom:6px">${line}</div>
      ${steps.length > 1 ? `<div style="display:flex;gap:2px;padding:2px;background:var(--card-2);border:1px solid var(--line);border-radius:9px">${seg}</div>` : ""}
      ${can && maxWarn ? `<div class="note t-warn">Max: ${c.h(maxWarn.join(" "))}</div>` : ""}
      ${d.last && d.last.message ? `<div class="note">${c.h(d.last.message)}</div>` : ""}
      ${d.note ? `<div class="note">${c.h(d.note)}</div>` : ""}${foot}`;
  },
});
