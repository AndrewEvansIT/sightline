(function () {
  const COLORS = { claude: "var(--s1)", codex: "var(--s2)", gemini: "var(--s3)", aider: "var(--s4)" };
  const count = (n) => {
    n = Number(n || 0);
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}K`;
    return String(Math.round(n));
  };
  const usd = (v) => (v == null ? "–" : v >= 100 ? `$${Math.round(v)}` : `$${v.toFixed(2)}`);
  const windowName = (m) => (m === 300 ? "5-hour" : m === 10080 ? "weekly" : m ? `${Math.round(m / 60)}-hour` : "");

  Sightline.register("agents", {
    render(d, c) {
      const sessions = d.sessions || [];
      const list = sessions.length
        ? sessions.map((s) => `<div class="row"><span class="dot" style="background:${COLORS[s.key] || "var(--s6)"}"></span>
            <span class="stack"><span class="lbl">${c.h(s.project || "Unknown folder")}</span><span class="sub">${c.h(s.agent)}</span></span>
            <span class="val">${s.mem ? c.bytes(s.mem) : ""}${s.secs != null ? ` · ${c.h(c.dur(s.secs))}` : ""}</span></div>`).join("")
        : `<div class="empty">No coding agents running.</div>`;

      const t = d.tokens || {};
      let tokens;
      if (d.scanning) {
        tokens = `<div class="empty">Reading today's logs…</div>`;
      } else if (!t.total) {
        tokens = `<div class="empty">No tokens used yet today.</div>`;
      } else {
        const max = Math.max(...t.by_agent.map((a) => a.tokens), 1);
        tokens = t.by_agent.map((a) => {
          const fresh = a.input + a.output + a.cache_write + a.cache_write_1h;
          const models = a.models.map((m) => `${m.model}: ${count(m.tokens)}${m.usd != null ? " (" + usd(m.usd) + ")" : ""}`).join("\n");
          return `<div class="meter" title="${c.h(models)}"><div class="line"><span>${c.h(a.agent)}</span>
              <span>${count(a.tokens)}${a.priced || a.usd ? ` · ${usd(a.usd)}${a.priced ? "" : "+"}` : ""}</span></div>
            <div class="track"><i style="width:${(a.tokens / max) * 100}%;background:${COLORS[a.key] || "var(--s6)"}"></i></div>
            <div style="font-size:11px;color:var(--faint);margin-top:2px">${count(fresh)} new · ${count(a.cache_read)} reread from cache · ${count(a.output)} written</div></div>`;
        }).join("");
        tokens += `<div class="row" style="margin-top:2px"><span class="lbl">Total today</span><span class="val">${count(t.total)} tokens · ${usd(t.usd)}</span></div>`;
      }

      const limits = t.limits && (t.limits.windows || []).filter((w) => w.resets && w.resets * 1000 > Date.now() && w.pct != null);
      const limitLine = limits && limits.length
        ? `<div class="note">Codex plan limits: ${limits.map((w) => `${c.num(w.pct, 0)}% of the ${windowName(w.minutes)} limit used`).join(", ")}.</div>` : "";
      const unpriced = (t.unpriced || []).length ? ` No price listed for ${t.unpriced.map(c.h).join(", ")}.` : "";

      return `<div class="sep">Running now</div>${list}
        <div class="sep">Tokens today</div>${tokens}${limitLine}
        <div class="note">Dollars are API-equivalent: what these tokens would cost at API list prices (as of ${c.h(d.prices_as_of || "")}).
          Subscriptions aren't billed this way.${unpriced}</div>`;
    },
  });
})();
