// Everything that talks to the engine and writes the store. Components call these; they never fetch directly.
import { api, stream, ApiError } from "./api.js";
import { get, set, persist } from "./store.js";
import { debounce, RIGHTS_OK } from "./util.js";

// ------------------------------------------------------------- notices
let noticeTimer;
export function notify(n) {
  clearTimeout(noticeTimer);
  set({ notice: n ? { id: Date.now(), ...n } : null });
  if (n && n.tone !== "bad" && !n.sticky) noticeTimer = setTimeout(() => set({ notice: null }), n.ms || 4200);
}
export function fail(e, title) {
  if (e instanceof ApiError && e.offline) {
    markOffline();
    return;
  }
  notify({
    tone: "bad",
    title: title || e.message || "Something failed",
    detail: e.detail && e.message !== e.detail ? (title ? e.message + " — " + e.detail : e.detail) : title ? e.message : "",
    fix: e.fix || "",
  });
}

// ------------------------------------------------------------- engine
let pingTimer;
function markOffline() {
  const en = get().engine;
  if (en.online !== false) set({ engine: { ...en, online: false }, live: false });
  schedulePing(4000);
}
function schedulePing(ms) {
  clearTimeout(pingTimer);
  pingTimer = setTimeout(checkEngine, ms);
}
let started = false;
export async function checkEngine() {
  try {
    const p = await api.ping();
    const was = get().engine.online;
    set({ engine: { online: true, version: p?.version || "", root: p?.root || "" } });
    if (was !== true) await boot();
    schedulePing(15000);
  } catch (e) {
    markOffline();
  }
}

let stopStream;
async function boot() {
  if (!started) {
    started = true;
    stopStream = stream(onEvent, (live) => {
      set({ live });
      if (live) refreshAll();
    });
  }
  await refreshAll();
}

export async function refreshAll() {
  await Promise.all([loadCreators(), loadSources(), loadIntegrations(), loadOverview(), loadJobs(), quiet(loadSettings)]);
  const s = get();
  if (s.sourceId && !s.sources.some((x) => String(x.id) === String(s.sourceId))) set({ sourceId: null });
  if (get().sourceId) await selectSource(get().sourceId, { keepClip: true });
}

// ------------------------------------------------------------- loaders (each tolerant)
const quiet = async (fn, fallback) => {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.offline) markOffline();
    return fallback;
  }
};
export async function loadCreators() {
  set({ creators: (await quiet(api.creators, null)) ?? get().creators });
}
export async function loadSources() {
  const r = await quiet(() => api.sources(), null);
  if (r) set({ sources: r, sourcesLoaded: true });
}
export async function loadIntegrations() {
  set({ integrations: (await quiet(api.integrations, null)) ?? get().integrations });
}
export async function loadOverview() {
  const [o, top] = await Promise.all([
    quiet(api.overview, null),
    quiet(() => api.clips({ status: "rendered,approved,exported", sort: "score" }), null),
  ]);
  if (o) set({ overview: { ...o, top_all: Array.isArray(top) ? top : null } });
}
export async function loadJobs() {
  set({ jobs: (await quiet(() => api.jobs(), null)) ?? get().jobs });
}
export async function loadSettings() {
  const st = await quiet(api.settings, null);
  if (st) set({ settings: st });
}

// ------------------------------------------------------------- source
export async function selectSource(id, { keepClip } = {}) {
  const prev = get().sourceId;
  set({ sourceId: id });
  persist("sourceId", id);
  if (!id) {
    set({ source: null, timeline: null, clips: [], clipId: null, clip: null, selection: new Set(), clipsLoaded: false });
    return;
  }
  if (String(prev) !== String(id) && !keepClip) {
    set({ clipId: null, clip: null, selection: new Set(), timeline: null, clips: [], clipsLoaded: false });
  }
  await Promise.all([loadSource(), loadClips(), loadTimeline()]);
  // default: select the best clip so the phone has something real to play
  const s = get();
  if (!s.clipId && s.clips.length) {
    const vis = s.clips.filter((c) => c.status !== "rejected" && !c.duplicate_of);
    const best = (vis.length ? vis : s.clips).slice().sort((a, b) => (b.overall || 0) - (a.overall || 0))[0];
    if (best) selectClip(best.id);
  }
}

export async function loadSource() {
  const id = get().sourceId;
  if (!id) return;
  try {
    const src = await api.source(id);
    if (String(get().sourceId) === String(id)) set({ source: src });
  } catch (e) {
    if (e.status === 404) {
      set({ sourceId: null, source: null });
      persist("sourceId", null);
    } else if (e.offline) markOffline();
  }
}

export async function loadTimeline() {
  const id = get().sourceId;
  if (!id) return;
  try {
    const t = await api.timeline(id);
    if (String(get().sourceId) === String(id)) set({ timeline: t && typeof t === "object" ? t : null });
  } catch {
    if (String(get().sourceId) === String(id)) set({ timeline: null });
  }
}

export function sourceJobs() {
  const s = get();
  const fromDetail = s.source?.jobs || [];
  const fromList = s.jobs.filter((j) => String(j.source_id) === String(s.sourceId));
  const byId = new Map();
  for (const j of [...fromList, ...fromDetail]) byId.set(j.id, { ...(byId.get(j.id) || {}), ...j });
  return [...byId.values()].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || b.id - a.id);
}
export function activeJob() {
  return sourceJobs().find((j) => j.status === "running" || j.status === "queued") || null;
}
export function lastJob() {
  return sourceJobs()[0] || null;
}

// ------------------------------------------------------------- clips
export async function loadClips() {
  const s = get();
  const q = { status: "all", sort: s.sort === "time" ? "time" : s.sort === "duration" ? "duration" : "score" };
  if (s.filters.scope !== "all") {
    if (!s.sourceId) {
      set({ clips: [], clipsLoaded: true });
      return;
    }
    q.source_id = s.sourceId;
  }
  try {
    const clips = await api.clips(q);
    set({ clips: Array.isArray(clips) ? clips : clips?.clips || [], clipsLoaded: true });
    const cur = get().clipId;
    if (cur && !get().clips.some((c) => c.id === cur)) set({ clipId: null, clip: null });
  } catch (e) {
    if (e.offline) markOffline();
    set({ clipsLoaded: true });
  }
}

export async function selectClip(id, { open } = {}) {
  if (id == null) {
    set({ clipId: null, clip: null });
    return;
  }
  const s = get();
  const base = s.clips.find((c) => c.id === id);
  set({ clipId: id, clip: base && s.clip?.id === id ? { ...s.clip, ...base } : base || null });
  if (open) setInspector(true);
  if (base && String(base.source_id) !== String(s.sourceId) && s.filters.scope === "all") {
    set({ sourceId: base.source_id });
    persist("sourceId", base.source_id);
    loadSource();
    loadTimeline();
  }
  await loadClipDetail(id);
}

export async function loadClipDetail(id = get().clipId) {
  if (id == null) return;
  try {
    const c = await api.clip(id);
    if (get().clipId === id) set({ clip: c });
  } catch (e) {
    if (e.offline) markOffline();
  }
}

export function setInspector(open) {
  set({ inspector: open });
  persist("inspector", open);
}

function patchLocal(c) {
  const s = get();
  set({ clips: s.clips.map((x) => (x.id === c.id ? { ...x, ...c } : x)) });
  if (s.clipId === c.id) set({ clip: { ...(get().clip || {}), ...c } });
}

export async function patchClip(id, body, { quietly } = {}) {
  try {
    const c = await api.patchClip(id, body);
    if (c && c.id != null) patchLocal(c);
    else patchLocal({ id, ...body });
    return c;
  } catch (e) {
    if (!quietly) fail(e, "Couldn't save the clip");
    throw e;
  }
}

export async function setStatus(ids, status) {
  const verb = status === "approved" ? "Approved" : status === "rejected" ? "Rejected" : "Updated";
  let ok = 0;
  let last;
  for (const id of ids) {
    try {
      await patchClip(id, { status }, { quietly: true });
      ok++;
    } catch (e) {
      last = e;
    }
  }
  if (last) fail(last, ok ? `${verb} ${ok}, ${ids.length - ok} failed` : `Couldn't change status`);
  else notify({ tone: "ok", title: `${verb} ${ok === 1 ? "1 clip" : ok + " clips"}` });
  loadOverview();
}

export async function rerender(id, edits) {
  try {
    if (edits && Object.keys(edits).length) await api.patchClip(id, edits);
    await api.renderClip(id);
    patchLocal({ id, ...(edits || {}), status: "rendering" });
    notify({ tone: "live", title: "Re-render started" });
  } catch (e) {
    fail(e, "Couldn't start the re-render");
  }
}

export async function makeVariants(id) {
  try {
    const r = await api.variants(id);
    notify({ tone: "live", title: "Generating hook variants", detail: Array.isArray(r) ? `${r.length} on the way` : "" });
    loadClips();
  } catch (e) {
    fail(e, "Couldn't make variants");
  }
}

export async function exportClips(ids) {
  if (!ids.length) return;
  const s = get();
  const clips = ids.map((id) => s.clips.find((c) => c.id === id)).filter(Boolean);
  try {
    const r = await api.export(ids);
    set({ lastExport: r });
    notify({
      tone: "ok",
      sticky: true,
      title: `Exported ${ids.length === 1 ? "1 clip" : ids.length + " clips"}`,
      path: r?.dir || "",
      detail: "MP4, captions (.srt) and metadata (.json) for each clip.",
    });
    loadClips();
    loadOverview();
  } catch (e) {
    if (e.offline) return markOffline();
    const blocked = clips.filter((c) => !RIGHTS_OK.includes(c.rights_status || sourceRights(c)));
    const creator = blocked[0]?.creator_name || clips[0]?.creator_name;
    notify({
      tone: "bad",
      sticky: true,
      title: e.status === 403 || /right/i.test(e.message + e.detail) ? "Export blocked — rights not cleared" : e.message,
      detail: e.detail || (creator ? `${creator}'s footage isn't cleared for export.` : ""),
      fix: e.fix || "Set the creator's rights to owned, licensed or permission granted.",
      action: e.status === 403 || /right/i.test(e.message + e.detail) ? { label: "Open creators", href: "#/creators" } : null,
    });
  }
}
function sourceRights(c) {
  const src = get().sources.find((x) => x.id === c.source_id);
  return src?.rights_status;
}

// ------------------------------------------------------------- jobs
export async function generate() {
  const s = get();
  if (!s.sourceId) return;
  try {
    const job = await api.startJob(s.sourceId, { target: s.target, caption_preset: s.preset });
    if (job?.id) set({ jobs: [job, ...s.jobs.filter((j) => j.id !== job.id)] });
    await loadSource();
  } catch (e) {
    fail(e, "Couldn't start processing");
  }
}
export async function cancelJob(id) {
  try {
    await api.cancelJob(id);
    await Promise.all([loadSource(), loadJobs()]);
  } catch (e) {
    fail(e, "Couldn't cancel");
  }
}
export async function retryJob(id) {
  try {
    await api.retryJob(id);
    await Promise.all([loadSource(), loadJobs()]);
  } catch (e) {
    fail(e, "Couldn't retry");
  }
}

// ------------------------------------------------------------- live events
const refreshSource = debounce(() => {
  loadSource();
  loadJobs();
  loadTimeline();
}, 350);
const refreshClips = debounce(() => {
  loadClips();
  loadClipDetail();
  loadOverview();
}, 450);
const refreshLists = debounce(() => {
  loadSources();
  loadOverview();
}, 600);

function onEvent(d) {
  const type = d.type || d.kind || "";
  const payload = d.data && typeof d.data === "object" ? d.data : null;
  if (type === "hello") {
    if (payload?.jobs?.length) {
      const s0 = get();
      const ids = new Set(payload.jobs.map((j) => j.id));
      set({ jobs: [...payload.jobs, ...s0.jobs.filter((j) => !ids.has(j.id))] });
    }
    return;
  }
  const job = d.job || (type.startsWith("job") ? payload || d : null);
  const clip = d.clip || (type.startsWith("clip") ? payload || d : null);
  const s = get();

  if (job && job.id != null) {
    // merge the pushed job immediately for a snappy readout; then re-read authoritative rows
    if (job.stages || job.status) {
      const jobs = s.jobs.some((j) => j.id === job.id) ? s.jobs.map((j) => (j.id === job.id ? { ...j, ...job } : j)) : [job, ...s.jobs];
      set({ jobs });
      if (s.source?.jobs) set({ source: { ...s.source, jobs: s.source.jobs.map((j) => (j.id === job.id ? { ...j, ...job } : j)) } });
    }
    if (String(job.source_id) === String(s.sourceId) || job.source_id == null) {
      refreshSource();
      if (job.status === "done" || job.status === "failed" || /render|qc|score|dedupe|detect/.test(job.current_stage || "")) refreshClips();
    }
    refreshLists();
    return;
  }
  if (clip) {
    if (clip.id != null && clip.status) patchLocal(clip);
    refreshClips();
    if (String(clip.source_id) === String(s.sourceId)) refreshSource();
    return;
  }
  if (type === "source" || d.source) {
    refreshLists();
    refreshSource();
    return;
  }
  // activity events etc.
  refreshLists();
  if (/job|stage|clip|render/.test(type + " " + (d.event?.kind || d.kind || ""))) {
    refreshSource();
    refreshClips();
  }
}

// fallback while SSE is down and a job is running: re-read real state (never simulated)
setInterval(() => {
  const s = get();
  if (s.live || s.engine.online !== true) return;
  if (activeJob()) {
    loadSource();
    loadClips();
  }
}, 5000);

export { stopStream };
