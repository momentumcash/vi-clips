// Top bar: source selector · target length · caption preset · Generate (→ live stage readout while a job runs).
import { get, set, on, persist } from "../store.js";
import { esc, tc, dur, TARGETS, PRESETS, presetLabel, STAGES, STAGE_LABEL, rightsTone, RIGHTS_LABEL, icon, ago } from "../util.js";
import { selectSource, generate, cancelJob, retryJob, activeJob, lastJob } from "../actions.js";
import { openImport } from "./importer.js";

export function mountSourcebar(root) {
  root.classList.add("topbar");
  root.innerHTML = `
    <div class="src-pick" data-pop>
      <button class="src-btn" type="button" aria-haspopup="listbox" aria-expanded="false"></button>
      <div class="pop src-pop" role="listbox" hidden></div>
    </div>
    <div class="tb-sep"></div>
    <div class="tb-field" title="Settings for the next Generate — clips already made keep theirs">
      <span class="tb-label">Next run</span>
      <div class="seg" role="radiogroup" aria-label="Target length for the next run">
        ${TARGETS.map(([k, l]) => `<button type="button" role="radio" data-target="${k}">${l}</button>`).join("")}
      </div>
    </div>
    <div class="tb-field">
      <div class="select">
        <select aria-label="Caption style for the next run">${PRESETS.map(([k, l]) => `<option value="${k}">${l} captions</option>`).join("")}</select>
        ${icon.chev}
      </div>
    </div>
    <div class="tb-run"></div>`;

  const btn = root.querySelector(".src-btn");
  const pop = root.querySelector(".src-pop");
  const sel = root.querySelector("select");
  const run = root.querySelector(".tb-run");

  btn.addEventListener("click", () => togglePop(!pop.hidden ? false : true));
  function togglePop(open) {
    pop.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) renderPop();
  }
  document.addEventListener("pointerdown", (e) => {
    if (!pop.hidden && !root.querySelector(".src-pick").contains(e.target)) togglePop(false);
  });
  pop.addEventListener("click", (e) => {
    const b = e.target.closest("[data-src]");
    if (b) {
      togglePop(false);
      selectSource(b.dataset.src === "" ? null : coerce(b.dataset.src));
    } else if (e.target.closest("[data-import]")) {
      togglePop(false);
      openImport();
    }
  });
  root.querySelector(".seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-target]");
    if (!b) return;
    set({ target: b.dataset.target });
    persist("target", b.dataset.target);
  });
  sel.addEventListener("change", () => {
    set({ preset: sel.value });
    persist("preset", sel.value);
  });
  run.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]");
    if (!a) return;
    const id = a.dataset.job ? coerce(a.dataset.job) : null;
    if (a.dataset.act === "generate") generate();
    if (a.dataset.act === "cancel") cancelJob(id);
    if (a.dataset.act === "retry") retryJob(id);
  });

  function renderBtn() {
    const s = get();
    const src = s.source || s.sources.find((x) => String(x.id) === String(s.sourceId));
    if (!src && !s.sourcesLoaded && s.engine.online !== false) {
      btn.innerHTML = `<span class="src-thumb empty"></span><span class="src-txt"><span class="tb-skel sm"></span></span>`;
      return;
    }
    if (!src) {
      const off = s.engine.online === false && !s.sources.length;
      btn.innerHTML = `<span class="src-thumb empty"></span><span class="src-txt"><b>${off ? "Engine offline" : s.sources.length ? "Choose a source" : "No sources yet"}</b><small>${off ? "Sources load when it's back" : s.sources.length ? s.sources.length + " in library" : "Import a video to start"}</small></span>${icon.chev}`;
      return;
    }
    const r = src.rights_status || "unknown";
    btn.innerHTML = `
      ${thumb(src)}
      <span class="src-txt">
        <b>${esc(src.title || "Untitled")}</b>
        <small>${esc(creatorName(src))}<i>·</i><span class="mono">${tc(src.duration_s)}</span><i>·</i><span class="dot t-${rightsTone(r)}" title="Rights: ${esc(RIGHTS_LABEL[r] || r)}"></span>${esc(RIGHTS_LABEL[r] || r)}</small>
      </span>${icon.chev}`;
  }

  function renderPop() {
    const s = get();
    const rows = s.sources
      .slice()
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
      .map(
        (src) => `
        <button type="button" class="src-row ${String(src.id) === String(s.sourceId) ? "on" : ""}" data-src="${esc(src.id)}" role="option">
          ${thumb(src)}
          <span class="src-txt"><b>${esc(src.title || "Untitled")}</b><small>${esc(creatorName(src))}<i>·</i><span class="mono">${tc(src.duration_s)}</span></small></span>
          <span class="src-state"><span class="dot t-${srcTone(src.status)}"></span>${esc(srcLabel(src.status))}</span>
        </button>`
      )
      .join("");
    pop.innerHTML = `
      <div class="pop-list">${rows || `<p class="pop-empty">Nothing imported yet.</p>`}</div>
      <div class="pop-foot">
        ${s.sourceId ? `<button type="button" class="link" data-src="">Studio overview</button>` : "<span></span>"}
        <button type="button" class="btn ghost sm" data-import>Import video</button>
      </div>`;
  }

  function renderControls() {
    const s = get();
    root.querySelectorAll("[data-target]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.target === s.target)));
    sel.value = s.preset;
  }

  function renderRun() {
    const s = get();
    if (!s.sourceId && !s.sourcesLoaded) {
      run.innerHTML = `<span class="tb-skel" aria-label="Loading"></span>`;
      return;
    }
    if (!s.sourceId) {
      run.innerHTML = `<button class="btn primary" type="button" disabled title="Choose a source first">Generate clips</button>`;
      return;
    }
    const job = activeJob();
    if (job) {
      run.innerHTML = readout(job);
      return;
    }
    // don't guess before the source's real status is known
    if (!s.source || String(s.source.id) !== String(s.sourceId) || !s.clipsLoaded) {
      run.innerHTML = `<span class="tb-skel" aria-label="Loading"></span>`;
      return;
    }
    const last = lastJob();
    const n = s.clips.length;
    if (last && last.status === "failed") {
      const st = (last.stages || []).find((x) => x.status === "failed");
      const where = STAGE_LABEL[st?.name || last.current_stage] || st?.name || last.current_stage;
      const centerShowsIt = !n; // the studio's failed state carries the error and the one Retry
      run.innerHTML = `
        <div class="readout failed">
          <span class="dot t-bad"></span>
          <span class="ro-txt"><b>${where ? "Failed at " + esc(where) : "Processing failed"}</b></span>
          ${centerShowsIt ? "" : `<button class="btn" type="button" data-act="retry" data-job="${esc(last.id)}">Retry</button>`}
        </div>`;
      return;
    }
    if (s.source?.status === "blocked") {
      run.innerHTML = `<button class="btn primary" type="button" disabled>Generate clips</button>`;
      return;
    }
    const processed = n || s.source.status === "processed" || (last && last.status === "done") || Object.keys(s.source.clip_counts || {}).length;
    run.innerHTML = processed
      ? `<button class="btn" type="button" data-act="generate" title="Run the pipeline again with these settings">Generate again</button>`
      : `<button class="btn primary" type="button" data-act="generate">Generate clips</button>`;
  }

  function readout(job) {
    const stages = normStages(job);
    const cur = stages.find((x) => x.status === "running") || stages.find((x) => x.status === "pending");
    const idx = cur ? stages.indexOf(cur) : stages.length;
    const pct = cur && cur.progress > 0 ? Math.round(cur.progress * 100) : null;
    const t0 = cur?.started_at ? Date.parse(cur.started_at) : NaN;
    const el = cur?.status === "running" && isFinite(t0) ? Math.max(0, (Date.now() - t0) / 1000) : null;
    const queued = job.status === "queued";
    return `
      <div class="readout" aria-live="polite">
        <span class="ro-txt">
          <b>${queued ? "Queued" : esc(STAGE_LABEL[cur?.name] || cur?.name || "Working")}${!queued && pct != null ? ` <span class="mono">${pct}%</span>` : !queued && el != null ? ` <span class="mono">${tc(el)}</span>` : ""}</b>
          <small class="mono">${queued ? "waiting for the worker" : `${Math.min(idx + 1, stages.length)}/${stages.length}${cur?.detail && !/%/.test(cur.detail) ? " · " + esc(cur.detail) : ""}`}</small>
        </span>
        <span class="ro-ticks" aria-hidden="true">${stages
          .map((x) => `<i class="k-${x.status}" style="--p:${x.status === "running" ? x.progress || 0 : x.status === "done" || x.status === "skipped" ? 1 : 0}" title="${esc(STAGE_LABEL[x.name] || x.name)}"></i>`)
          .join("")}</span>
        <button class="btn ghost sm" type="button" data-act="cancel" data-job="${esc(job.id)}">Cancel</button>
      </div>`;
  }

  const r1 = on(["sources", "sourcesLoaded", "source", "sourceId", "creators", "engine"], () => {
    renderBtn();
    renderRun();
    if (!pop.hidden) renderPop();
  });
  const r2 = on(["target", "preset"], renderControls);
  const r3 = on(["jobs", "clips", "clipsLoaded"], renderRun);
  const tick = setInterval(() => activeJob() && renderRun(), 1000);
  renderBtn();
  renderControls();
  renderRun();
  return () => {
    r1();
    r2();
    r3();
    clearInterval(tick);
  };
}

export function normStages(job) {
  const by = new Map((job.stages || []).map((x) => [x.name, x]));
  return STAGES.map((n) => ({ name: n, status: "pending", progress: 0, ...(by.get(n) || {}) }));
}
function creatorName(src) {
  if (src.creator_name) return src.creator_name;
  const c = get().creators.find((x) => x.id === src.creator_id);
  return c?.name || "No creator";
}
function thumb(src) {
  const u = src.thumb_url || (src.thumb_key ? "/media/" + src.thumb_key : "");
  return u ? `<span class="src-thumb"><img src="${esc(u)}" alt="" loading="lazy"></span>` : `<span class="src-thumb empty"></span>`;
}
const coerce = (v) => (/^\d+$/.test(v) ? Number(v) : v);
export const srcLabel = (s) =>
  ({ new: "Not processed", ready: "Not processed", processing: "Processing", processed: "Processed", failed: "Failed", blocked: "Blocked" }[s] || s || "—");
export const srcTone = (s) => ({ processing: "live", processed: "ok", failed: "bad", blocked: "warn" }[s] || "idle");
