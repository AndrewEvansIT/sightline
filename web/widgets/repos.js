(function () {
  document.addEventListener("click", (ev) => {
    const r = ev.target.closest("[data-repo]");
    if (!r || ev.target.closest("[data-action]")) return;
    if (Sightline.ctx.readonly()) return;
    Sightline.ctx.api("/api/open", { target: "reveal:" + r.dataset.repo }).then((res) => {
      if (res && res.ok === false && res.message) Sightline.toast(res.message, "bad");
    }).catch(() => Sightline.toast("Couldn't reach Sightline.", "bad"));
  });

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one}`;

  Sightline.register("repos", {
    render(d, c) {
      const repos = d.repos || [];
      if (!repos.length) {
        return `<div class="empty">No loose ends in ${plural(d.count || 0, "repo", "repos")}.</div>
          <div class="note">Looking in ${c.h((d.roots || []).join(", "))}.</div>`;
      }
      const rows = repos.map((r) => {
        const chips = [];
        if (r.changed) chips.push(`<span class="chip warn">${r.changed} changed</span>`);
        if (r.untracked) chips.push(`<span class="chip">${r.untracked} new</span>`);
        if (r.ahead) chips.push(`<span class="chip warn">${r.ahead} to push</span>`);
        if (r.unpushed) chips.push(`<span class="chip warn" title="This branch has no upstream on the remote">${r.unpushed} not pushed</span>`);
        if (r.behind) chips.push(`<span class="chip" title="Behind the upstream, as of the last fetch">${r.behind} to pull</span>`);
        if (r.stashes) chips.push(`<span class="chip">${plural(r.stashes, "stash", "stashes")}</span>`);
        if (r.unchecked) chips.push(`<span class="chip" title="git status took too long, so this repo wasn't checked">too slow to check</span>`);
        if (!r.remote) chips.push(`<span class="chip" title="No remote set up">local only</span>`);
        const when = r.last_commit ? `last commit ${c.ago(r.last_commit)}` : "no commits yet";
        return `<div class="row clickable" data-repo="${c.h(r.path)}" title="Show in Finder">
            <span class="stack"><span class="lbl">${c.h(r.name)} <span class="t-faint">· ${c.h(r.branch || "")}</span></span>
            <span class="sub">${when}</span></span>
            <span class="val" style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;white-space:normal">${chips.join("")}</span></div>`;
      }).join("");
      const note = `${plural(d.count || 0, "repo", "repos")} checked${d.capped ? " (stopped at 200)" : ""}. Click a repo to show it in Finder.`;
      return `${rows}<div class="note">${note}</div>`;
    },
  });
})();
