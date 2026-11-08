/* Share cards: "my rig" and model speed results, drawn on a canvas and saved as a PNG (Pictures › Sightline). */
(function () {
  "use strict";
  const W = 1200, H = 630;
  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim() || "#4fb6c9";

  function base(ctx2d, title) {
    const g = ctx2d.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, "#0d0f12");
    g.addColorStop(1, "#161a20");
    ctx2d.fillStyle = g;
    ctx2d.fillRect(0, 0, W, H);
    // Soft accent glow.
    const r = ctx2d.createRadialGradient(W - 160, 120, 10, W - 160, 120, 420);
    r.addColorStop(0, "rgba(79,182,201,.22)");
    r.addColorStop(1, "rgba(79,182,201,0)");
    ctx2d.fillStyle = r;
    ctx2d.fillRect(0, 0, W, H);
    ctx2d.fillStyle = "#8b9199";
    ctx2d.font = "600 26px -apple-system, system-ui, sans-serif";
    ctx2d.fillText(title, 64, 82);
    // Footer mark.
    ctx2d.fillStyle = "#5a6068";
    ctx2d.font = "500 22px -apple-system, system-ui, sans-serif";
    ctx2d.fillText("Sightline", 64, H - 52);
    ctx2d.textAlign = "right";
    ctx2d.fillText(new Date().toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }), W - 64, H - 52);
    ctx2d.textAlign = "left";
  }

  function stat(ctx2d, x, y, value, unit, label, color) {
    ctx2d.fillStyle = color || "#eceef1";
    ctx2d.font = "600 64px -apple-system, system-ui, sans-serif";
    ctx2d.fillText(value, x, y);
    const w = ctx2d.measureText(value).width;
    if (unit) {
      ctx2d.fillStyle = "#8b9199";
      ctx2d.font = "500 26px -apple-system, system-ui, sans-serif";
      ctx2d.fillText(unit, x + w + 8, y);
    }
    ctx2d.fillStyle = "#5a6068";
    ctx2d.font = "500 20px -apple-system, system-ui, sans-serif";
    ctx2d.fillText(label.toUpperCase(), x, y + 34);
  }

  function rig() {
    const S = window.Sightline.ctx;
    const sys = S.data("system") || {}, mem = S.data("memory") || {}, ai = S.data("ai_runtimes") || {}, lib = S.data("ai_library") || {};
    const power = S.data("power") || {}, store = S.data("storage") || {};
    const info = sys.info || {};
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const x = canvas.getContext("2d");
    base(x, "MY RIG");
    x.fillStyle = "#eceef1";
    x.font = "700 72px -apple-system, system-ui, sans-serif";
    x.fillText(info.chip || "Mac", 64, 190);
    x.fillStyle = "#8b9199";
    x.font = "500 30px -apple-system, system-ui, sans-serif";
    const cores = [info.p_cores && info.e_cores != null ? `${info.p_cores + info.e_cores}-core CPU` : null, info.gpu_cores ? `${info.gpu_cores}-core GPU` : null, info.memory_gb ? `${info.memory_gb} GB memory` : null].filter(Boolean).join("  ·  ");
    x.fillText(cores, 64, 240);
    const models = (ai.models || []).length;
    const stats = [
      [String(Math.round(mem.used_gb || 0)), `/ ${mem.total_gb || "?"} GB`, "memory in use", "#ff9f6b"],
      [String(models), models === 1 ? "model" : "models", "loaded now", "#c58cff"],
      [lib.total_gb != null ? String(Math.round(lib.total_gb)) : String((lib.models || []).length || "–"), lib.total_gb != null ? "GB" : "", "of models on disk", "#4fb6c9"],
      [power.total_w != null ? String(Math.round(power.total_w)) : sys.temp != null ? String(sys.temp) : "–", power.total_w != null ? "W" : "°C", power.total_w != null ? "power right now" : "chip temperature", "#32d74b"],
    ];
    stats.forEach((s, i) => stat(x, 64 + i * 272, 400, s[0], s[1], s[2], s[3]));
    const names = (ai.models || []).slice(0, 3).map((m) => m.name || m.model).filter(Boolean);
    if (names.length) {
      x.fillStyle = "#8b9199";
      x.font = "500 24px -apple-system, system-ui, sans-serif";
      x.fillText("Running: " + names.join(", "), 64, 490);
    }
    return { canvas, name: "rig" };
  }

  function speed(r) {
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const x = canvas.getContext("2d");
    base(x, "LOCAL MODEL SPEED");
    x.fillStyle = "#eceef1";
    x.font = "700 60px -apple-system, system-ui, sans-serif";
    let name = String(r.model || "Model");
    while (x.measureText(name).width > W - 128 && name.length > 10) name = name.slice(0, -2);
    if (name !== String(r.model || "Model")) name += "…";
    x.fillText(name, 64, 180);
    x.fillStyle = "#8b9199";
    x.font = "500 28px -apple-system, system-ui, sans-serif";
    x.fillText([r.runtime, r.chip, r.memory_gb ? `${r.memory_gb} GB` : null].filter(Boolean).join("  ·  "), 64, 228);
    x.fillStyle = "#4fb6c9";
    x.font = "700 150px -apple-system, system-ui, sans-serif";
    const big = r.gen_tps != null ? String(Math.round(r.gen_tps)) : "–";
    x.fillText(big, 64, 410);
    const bw = x.measureText(big).width;
    x.fillStyle = "#8b9199";
    x.font = "500 34px -apple-system, system-ui, sans-serif";
    x.fillText(`tokens / second${r.approx ? " (approx.)" : ""}`, 64 + bw + 18, 410);
    const extra = [
      [r.prompt_tps != null ? String(Math.round(r.prompt_tps)) : "–", "tok/s", "reading the prompt"],
      [r.ttft_s != null ? r.ttft_s.toFixed(2) : "–", "s", "to the first word"],
    ];
    if (r.wh != null) extra.push([r.wh < 0.1 ? r.wh.toFixed(3) : r.wh.toFixed(2), "Wh", "energy per answer"]);
    extra.forEach((s, i) => stat(x, 64 + i * 330, 512, s[0], s[1], s[2]));
    return { canvas, name: `speed-${String(r.model || "model").split("/").pop()}` };
  }

  function open(kind, payload) {
    const made = kind === "speed" ? speed(payload || {}) : rig();
    const url = made.canvas.toDataURL("image/png");
    const ov = document.getElementById("overlay");
    ov.hidden = false;
    ov.innerHTML = `<div class="sheet" role="dialog" aria-label="Share card" style="width:min(760px,100%)">
      <div class="sheet-head"><h2>${kind === "speed" ? "Speed card" : "Rig card"}</h2><button class="btn quiet x" id="cd-close">Close</button></div>
      <div class="pane"><img class="card-preview" src="${url}" alt="Preview of the card">
      <div class="btns" style="margin-top:12px;justify-content:center"><button class="btn primary" id="cd-save">Save picture</button><button class="btn quiet" id="cd-copy">Copy</button></div></div></div>`;
    const close = () => { ov.hidden = true; ov.innerHTML = ""; };
    ov.onclick = (e) => { if (e.target === ov) close(); };
    document.getElementById("cd-close").onclick = close;
    document.getElementById("cd-save").onclick = async () => {
      const res = await window.Sightline.ctx.api("/api/card", { png: url, name: made.name });
      window.Sightline.toast(res.message || "Saved.", res.ok ? "" : "bad");
    };
    document.getElementById("cd-copy").onclick = () => {
      made.canvas.toBlob(async (blob) => {
        try {
          await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
          window.Sightline.toast("Copied. Paste it anywhere.");
        } catch (e) {
          window.Sightline.toast("Copying isn't allowed here. Use Save picture instead.", "bad");
        }
      });
    };
  }

  window.SightlineCards = { open };
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("btn-card").onclick = () => open("rig");
  });
})();
