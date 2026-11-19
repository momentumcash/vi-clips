// Analytics — real aggregates only. Predicted (ClipForge score) vs actual where metrics exist; manual entry.
import { get, on } from "../store.js";
import { api } from "../api.js";
import { esc, STATUS_LABEL, statusTone, icon } from "../util.js";
import { fail, notify, loadClips } from "../actions.js";

export function mount(el) {
  el.innerHTML = `
    <section class="pg">
      <header class="pg-head"><h1>Analytics</h1></header>
      <div class="pg-body an"></div>
    </section>`;
  const body = el.querySelector(".an");
  let data = null;
  let err = null;
  let allClips = [];

  async function load() {
    try {
      [data, allClips] = await Promise.all([api.analytics(), api.clips({ sort: "score" }).catch(() => [])]);
      err = null;
    } catch (e) {
      err = e;
    }
    render();
  }

  function num(v) {
    return v == null ? "—" : typeof v === "number" ? v.toLocaleString() : esc(v);
  }

  function render() {
    if (err) {
      body.innerHTML = `<div class="pg-empty"><p>${esc(err.message)}</p>${err.detail ? `<p class="muted">${esc(err.detail)}</p>` : ""}</div>`;
      return;
    }
    if (!data) {
      body.innerHTML = "";
      return;
    }
    const d = data;
    const exportsN = typeof d.exports === "object" && d.exports ? d.exports.count ?? d.exports.total : d.exports ?? d.exports_count;
    const clipsN = typeof d.exports === "object" && d.exports ? d.exports.clips : d.clips_exported;
    const byStatus = d.clips_by_status || d.by_status || {};
    const statusEntries = Array.isArray(byStatus) ? byStatus.map((x) => [x.status, x.count ?? x.n]) : Object.entries(byStatus);
    const totalClips = statusEntries.reduce((a, [, n]) => a + (Number(n) || 0), 0);
    const pairs = d.predicted_vs_actual || d.pairs || [];
    const groups = d.metrics || d.by || {};

    body.innerHTML = `
      <div class="ov-line">
        <span><b class="mono">${num(exportsN)}</b> exports</span>
        ${clipsN != null ? `<span><b class="mono">${num(clipsN)}</b> clips exported</span>` : ""}
        <span><b class="mono">${num(totalClips)}</b> clips made</span>
        <span><b class="mono">${num(pairs.length)}</b> with real numbers</span>
      </div>
      <div class="an-cols">
        <section>
          <h2>Clips by status</h2>
          ${statusEntries.length ? `<ul class="bars">${statusEntries
            .sort((a, b) => b[1] - a[1])
            .map(([k, n]) => `<li><span><span class="dot t-${statusTone(k)}"></span>${esc(STATUS_LABEL[k] || k)}</span><span class="bar"><i style="transform:scaleX(${totalClips ? n / totalClips : 0})"></i></span><span class="mono">${num(n)}</span></li>`)
            .join("")}</ul>` : `<p class="muted">No clips yet.</p>`}
          ${groupTables(groups)}
        </section>
        <section>
          <h2>Predicted vs actual</h2>
          ${pairs.length ? `<table class="tbl compact"><thead><tr><th>Clip</th><th>Platform</th><th class="num">Score</th><th class="num">Views</th><th class="num">Watch %</th></tr></thead><tbody>${pairs
            .map((p) => {
              const c = p.clip || p;
              const m = p.actual || p.metrics || p;
              return `<tr><td><b>${esc(c.title || "Clip " + (c.id ?? p.clip_id))}</b></td><td>${esc(m.platform || "—")}</td><td class="num mono">${num(p.predicted_score ?? p.predicted ?? c.overall)}</td><td class="num mono">${num(m.views)}</td><td class="num mono">${m.avg_watch_pct != null ? num(m.avg_watch_pct) + "%" : "—"}</td></tr>`;
            })
            .join("")}</tbody></table>` : `<div class="an-empty"><p>Connect a platform or enter views to compare.</p><p class="muted">The ClipForge score is an editorial estimate until real numbers calibrate it.</p></div>`}
          <h2>Enter views</h2>
          ${metricsForm()}
        </section>
      </div>`;
  }

  function groupTables(groups) {
    const keys = Object.keys(groups || {}).filter((k) => groups[k] && (Array.isArray(groups[k]) ? groups[k].length : Object.keys(groups[k]).length));
    return keys
      .map((k) => {
        const g = groups[k];
        const rows = Array.isArray(g) ? g : Object.entries(g).map(([name, v]) => ({ name, ...(typeof v === "object" ? v : { value: v }) }));
        const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((c) => c !== "name" && c !== "key");
        return `<h2>By ${esc(k.replace(/^by_/, "").replace(/_/g, " "))}</h2>
          <table class="tbl compact"><thead><tr><th></th>${cols.map((c) => `<th class="num">${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead>
          <tbody>${rows.map((r) => `<tr><td>${esc(r.name ?? r.key ?? "")}</td>${cols.map((c) => `<td class="num mono">${num(typeof r[c] === "number" ? Math.round(r[c] * 10) / 10 : r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      })
      .join("");
  }

  function metricsForm() {
    const clips = allClips.filter((c) => ["exported", "approved", "rendered"].includes(c.status));
    if (!clips.length) return `<p class="muted">Approve or export a clip first.</p>`;
    return `
      <form class="mform">
        <div class="select"><select name="clip" aria-label="Clip">${clips.map((c) => `<option value="${c.id}">${esc(c.title || "Clip " + c.id)} — ${c.overall ?? "–"}</option>`).join("")}</select>${icon.chev}</div>
        <div class="select"><select name="platform" aria-label="Platform">${[["tiktok", "TikTok"], ["youtube", "YouTube Shorts"], ["instagram", "Instagram Reels"], ["x", "X"], ["other", "Other"]].map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>${icon.chev}</div>
        <input name="views" type="number" min="0" placeholder="Views" required>
        <input name="likes" type="number" min="0" placeholder="Likes">
        <input name="comments" type="number" min="0" placeholder="Comments">
        <input name="shares" type="number" min="0" placeholder="Shares">
        <input name="avg_watch_pct" type="number" min="0" max="100" step="0.1" placeholder="Avg watch %">
        <button class="btn" type="submit">Save numbers</button>
      </form>`;
  }

  body.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const n = (k) => (f[k].value === "" ? null : Number(f[k].value));
    try {
      await api.addMetrics(Number(f.clip.value) || f.clip.value, {
        platform: f.platform.value,
        views: n("views"),
        likes: n("likes"),
        comments: n("comments"),
        shares: n("shares"),
        avg_watch_pct: n("avg_watch_pct"),
      });
      notify({ tone: "ok", title: "Saved numbers" });
      load();
    } catch (er) {
      fail(er, "Couldn't save numbers");
    }
  });

  load();
  return on(["clips"], () => {});
}
