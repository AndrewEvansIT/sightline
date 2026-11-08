Sightline.register("network", {
  render(d, c) {
    const ms = d.ping_ms;
    const tone = d.online === false ? (d.fails >= 3 ? "bad" : "warn") : ms == null ? "" : ms >= 100 ? "warn" : "ok";
    const latency = d.online === null
      ? `<div class="big"><div class="n">–</div><div class="l">Internet check is off</div></div>`
      : `<div class="big"><div class="n ${tone ? "t-" + tone : ""}">${ms == null ? "Offline" : c.num(ms, 0)}${ms == null ? "" : "<small>ms</small>"}</div>
          <div class="l">${ms == null ? `No answer from 1.1.1.1 (${d.fails} in a row)` : "Delay to the internet" + (d.loss > 0 ? ` · ${c.num(d.loss, 0)}% lost` : "")}</div></div>`;
    const pings = c.recent("network.ping");
    const top = `<div style="display:flex;gap:12px;align-items:center">
        <div style="flex:none">${latency}</div>
        <div style="flex:1;min-width:0"><div style="display:flex;justify-content:space-between;font-size:11px;color:var(--faint)"><span>Delay</span><span>10 min</span></div>
          ${c.spark(pings, { color: tone === "warn" ? "var(--warn)" : "var(--accent)" })}</div></div>`;

    // Speed test
    const sp = d.speed;
    const busy = d.speed_running;
    const btn = c.readonly() ? "" : `<button class="btn quiet" data-action="speedtest"${busy ? " disabled" : ""}>${busy ? "Testing…" : "Test now"}</button>`;
    const speed = sp
      ? `<div class="row"><span class="stack"><span class="lbl">Speed: ${c.num(sp.down, 0)} down · ${c.num(sp.up, 0)} up <span class="t-faint">Mbps</span></span>
          <span class="sub">Tested ${c.h(c.ago(sp.at))}${sp.rpm ? ` · responsiveness ${c.num(sp.rpm, 0)} RPM` : ""}${d.speed_hours ? "" : " · automatic tests are off"}</span></span>${btn}</div>`
      : `<div class="row"><span class="stack"><span class="lbl">Speed</span><span class="sub">${busy ? "Testing now, about 15 seconds…" : d.speed_hours ? `First test runs a couple of minutes after start, then every ${d.speed_hours} h` : "Automatic tests are off"}</span></span>${btn}</div>`;

    // Connection
    const cn = d.connection || {};
    const rows = [];
    if (cn.type) {
      const port = cn.port && cn.port !== cn.type ? cn.port : "";
      rows.push({ label: "Connected by", value: cn.type, sub: [port, cn.device].filter(Boolean).join(" · ") });
    } else {
      rows.push({ label: "Connected by", value: "Not connected", state: "bad" });
    }
    if (cn.ip) rows.push({ label: "This Mac", value: cn.ip });
    if (cn.router) rows.push({ label: "Router", value: cn.router });
    const w = d.wifi;
    if (w) {
      const signal = w.signal != null ? `${w.quality} signal (${w.signal} dBm)` : "";
      const bits = [w.band, w.channel ? `channel ${w.channel}` : "", w.width_mhz ? `${w.width_mhz} MHz` : "", w.generation, w.rate_mbps ? `${w.rate_mbps} Mbps link` : ""].filter(Boolean);
      rows.push({ label: w.ssid ? `Wi-Fi: ${w.ssid}` : w.primary ? "Wi-Fi" : "Wi-Fi (backup)", value: signal,
        state: w.quality === "weak" ? "warn" : w.quality ? "ok" : "", sub: bits.join(" · "),
        title: w.noise != null ? `Noise ${w.noise} dBm. Signal above −60 dBm is good, below −70 dBm is weak.` : "" });
    }
    if (d.public && d.public.ip) rows.push({ label: "Public IP", value: d.public.ip, sub: [d.public.country, d.public.colo ? `via Cloudflare ${d.public.colo}` : ""].filter(Boolean).join(" · "), title: "The address websites see for your connection." });

    // Chips: VPN and DNS
    const chips = [];
    const v = d.vpn || {};
    chips.push(v.on ? `<span class="chip ok" title="Your internet traffic goes through a VPN.">VPN on${v.name ? ` (${c.h(v.name)})` : ""}</span>`
                    : `<span class="chip" title="No VPN is connected.">No VPN</span>`);
    (d.dns || []).forEach((s) => chips.push(`<span class="chip" title="Looks up website names (DNS server)">DNS ${c.h(s.label || s.server)}${s.label ? ` <span class="t-faint">${c.h(s.server)}</span>` : ""}</span>`));

    return `${top}${speed}
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0">${chips.join("")}</div>
      ${c.rows(rows)}`;
  },
});
