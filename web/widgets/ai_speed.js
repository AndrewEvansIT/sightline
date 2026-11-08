(function () {
  let picked = null; // index of the chosen model, kept across redraws

  const paramsOf = (t) => (t ? { base_url: t.base_url, model: t.model, runtime: t.runtime, name: t.name } : {});

  document.addEventListener("change", (ev) => {
    const sel = ev.target.closest("select[data-speed-pick]");
    if (!sel) return;
    picked = sel.value;
    const d = Sightline.ctx.data("ai_speed") || {};
    const t = (d.targets || [])[Number(sel.value)];
    const btn = sel.parentNode.querySelector("[data-action=run]");
    if (btn && t) btn.dataset.params = JSON.stringify(paramsOf(t));
  });

  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-speed-card]");
    if (!b) return;
    const d = Sightline.ctx.data("ai_speed") || {};
    const r = d.last && !d.last.error ? d.last : null;
    if (!r) return;
    if (window.SightlineCards && window.SightlineCards.open) {
      window.SightlineCards.open("speed", { model: r.name || r.model, runtime: r.runtime, chip: r.chip, memory_gb: r.memory_gb,
        ttft_s: r.ttft_s, prompt_tps: r.prompt_tps, gen_tps: r.gen_tps, wh: r.wh, approx: r.approx, ts: r.ts });
    } else {
      Sightline.toast("Cards aren't available in this version.");
    }
  });

  Sightline.register("ai_speed", {
    render(d, c) {
      const targets = d.targets || [];
      if (picked == null || Number(picked) >= targets.length) picked = "0";
      const t = targets[Number(picked)];
      const busy = !!d.running;
      const picker = targets.length
        ? `<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
            <select data-speed-pick style="flex:1;min-width:0;background:var(--card-2);color:var(--text);border:1px solid var(--line-2);border-radius:8px;padding:4px 6px;font:inherit;font-size:12px">
              ${targets.map((x, i) => `<option value="${i}"${String(i) === picked ? " selected" : ""}>${c.h(x.label)}</option>`).join("")}
            </select>
            <button class="btn primary" data-action="run" data-params="${c.attr(paramsOf(t))}"${busy || c.readonly() ? " disabled" : ""} title="Sends the same short test prompt (about 500 words in, 256 tokens out) and times the answer">${busy ? "Running…" : "Run"}</button></div>`
        : `<div class="empty">Load a model in Ollama, LM Studio, llama.cpp or an MLX server to test its speed.</div>`;
      let last = "";
      const r = d.last;
      if (busy) {
        last = `<div class="empty">Testing ${c.h(d.running.name)}… (started ${c.h(c.ago(d.running.started))})</div>`;
      } else if (r && r.error) {
        last = `<div class="t-bad" style="font-size:12px">Test of ${c.h(r.name || r.model)} failed: ${c.h(r.error)}</div>`;
      } else if (r) {
        const big = [
          { label: "writing, tokens/s", value: c.num(r.gen_tps, r.gen_tps >= 100 ? 0 : 1) },
          { label: "reading, tokens/s", value: c.num(r.prompt_tps, 0) },
          { label: "first token", value: c.num(r.ttft_s, r.ttft_s >= 10 ? 0 : 2), unit: "s" },
        ];
        if (r.wh != null) big.push({ label: "per answer", value: c.num(r.wh * 1000, 1), unit: "mWh" });
        last = `${c.bigs(big)}<div class="note" style="margin-top:0">${c.h(r.name || r.model)} · ${c.h(r.runtime || "")} · ${c.h(c.ago(r.ts))}${r.approx ? " · approximate (the server didn't report token counts)" : ""}</div>
          <div class="btns"><button class="btn quiet" data-speed-card title="Makes a picture of this result to share">Make card</button></div>`;
      }
      const hist = (d.history || []).slice(0, 8);
      const table = hist.length > 1 ? `<div class="sep">Earlier runs</div><table class="grid"><tr><th>Model</th><th class="num">Write</th><th class="num">Read</th><th class="num">First</th><th class="num">When</th></tr>
        ${hist.map((x) => `<tr><td style="max-width:140px;overflow:hidden;text-overflow:ellipsis" title="${c.h(x.model)}">${c.h(x.name || x.subject)}</td><td class="num">${c.num(x.gen_tps, 0)}</td><td class="num">${c.num(x.prompt_tps, 0)}</td><td class="num">${c.num(x.ttft_s, 1)} s</td><td class="num t-faint">${c.h(c.ago(x.ts))}</td></tr>`).join("")}</table>` : "";
      return picker + last + table;
    },
  });
})();
