// Queue — every job, its per-stage timeline, durations, cost; retry / cancel.
import { get, on } from "../store.js";
import { esc, dur, ago, STAGE_LABEL, TARGETS, presetLabel, humanError, icon } from "../util.js";
import { loadJobs, cancelJob, retryJob } from "../actions.js";
import { stageList, jobSummary } from "../components/queue.js";
import { normStages } from "../components/sourcebar.js";

const TONE = { queued: "idle", running: "live", done: "ok", failed: "bad", cancelled: "off" };
const LABEL = { queued: "Queued", running: "Running", done: "Done", failed: "Failed", cancelled: "Cancelled" };

export function mount(el) {
  el.innerHTML = `
    <section class="pg">
      <header class="pg-head"><h1>Queue</h1><span class="pg-sub mono"></span></header>
      <div class="pg-body"><div class="jobs"></div></div>
    </section>`;
  const list = el.querySelector(".jobs");
  const sub = el.querySelector(".pg-sub");
  const open = new Set();

  function render() {
    const s = get();
    const jobs = s.jobs.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || b.id - a.id);
    const running = jobs.filter((j) => j.status === "running").length;
    const queued = jobs.filter((j) => j.status === "queued").length;
    const cost = jobs.reduce((a, j) => a + (Number(j.cost_usd) || 0), 0);
    sub.textContent = `${running} running · ${queued} queued · $${cost.toFixed(2)} spent`;
    if (!jobs.length) {
      list.innerHTML = `<div class="pg-empty"><p>No jobs yet.</p><p class="muted">Choose a source in Studio and press Generate clips.</p></div>`;
      return;
    }
    list.innerHTML = jobs
      .map((j) => {
        const src = s.sources.find((x) => x.id === j.source_id);
        const st = normStages(j);
        const cur = st.find((x) => x.status === "running") || st.find((x) => x.status === "failed");
        const sum = jobSummary(j);
        const newest = !jobs.some((o) => o.source_id === j.source_id && o.id > j.id);
        const superseded = (j.status === "failed" || j.status === "cancelled") && jobs.some((o) => o.source_id === j.source_id && o.id > j.id && o.status === "done");
        const isOpen = open.has(j.id) || j.status === "running" || (j.status === "failed" && newest);
        const started = (j.stages || []).some((x) => x.status !== "pending");
        return `
          <article class="job ${isOpen ? "open" : ""} ${superseded ? "superseded" : ""}" data-id="${esc(j.id)}">
            <button class="job-row" type="button" data-toggle aria-expanded="${isOpen}">
              <span class="dot t-${TONE[j.status] || "idle"}"></span>
              <span class="job-t"><b>${esc(src?.title || j.source_title || "Source " + j.source_id)}</b><small>${esc(TARGETS.find((t) => t[0] === j.settings?.target)?.[1] || "Auto")} · ${esc(presetLabel(j.settings?.caption_preset))}</small></span>
              <span class="job-s">${superseded ? "Superseded" : esc(LABEL[j.status] || j.status)}${cur && j.status !== "done" && !superseded ? " · " + esc(STAGE_LABEL[cur.name] || cur.name) : ""}</span>
              <span class="mono job-n">${sum.total ? dur(sum.total) : "—"}</span>
              <span class="mono job-n">${j.cost_usd != null ? "$" + Number(j.cost_usd).toFixed(3) : "—"}</span>
              <span class="muted job-n">${esc(ago(j.created_at))}</span>
            </button>
            ${isOpen ? `<div class="job-detail">${j.error ? `<p class="st-err">${esc(humanError(j.error, STAGE_LABEL[cur?.name] || cur?.name || ""))}</p><details class="fold det"><summary><span class="fold-t">Details</span>${icon.chev}</summary><pre class="mono raw-err">${esc(j.error)}</pre></details>` : ""}${started ? stageList(j) : ""}
              <div class="job-acts">
                ${j.status === "running" || j.status === "queued" ? `<button class="btn sm" type="button" data-cancel>Cancel</button>` : ""}
                ${(j.status === "failed" || j.status === "cancelled") && !superseded ? `<button class="btn primary sm" type="button" data-retry>Retry</button>` : ""}
              </div></div>` : ""}
          </article>`;
      })
      .join("");
  }

  list.addEventListener("click", (e) => {
    const art = e.target.closest(".job");
    if (!art) return;
    const id = /^\d+$/.test(art.dataset.id) ? Number(art.dataset.id) : art.dataset.id;
    if (e.target.closest("[data-cancel]")) return cancelJob(id);
    if (e.target.closest("[data-retry]")) return retryJob(id);
    if (e.target.closest("[data-toggle]")) {
      open.has(id) ? open.delete(id) : open.add(id);
      render();
    }
  });
  const off = on(["jobs", "sources"], render);
  render();
  loadJobs();
  const t = setInterval(() => {
    if (get().jobs.some((j) => j.status === "running")) render(); // tick running durations from real start times
  }, 1000);
  return () => {
    off();
    clearInterval(t);
  };
}
