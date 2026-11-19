// The generating "show": progress, found moments popping in with their real scores, clips dropping into the
// grid as they render, and one result card. Driven only by real data:
//  - LIVE: the engine's job stages, clip rows and SSE refreshes (plus /api/jobs/:id/live words if the engine has it)
//  - REPLAY: a finished job's recorded stage times, its real clips and their recorded "Clip ready" events,
//    compressed to ~20 s and labelled "Replay" on screen.
import { get, set, on } from "../store.js";
import { api } from "../api.js";
import { esc, tc, displayTitle, STAGE_LABEL } from "../util.js";
import { activeJob, sourceJobs, selectClip, notify, fail } from "../actions.js";
import { normStages } from "../components/sourcebar.js";
import { phone2 } from "./phone.js";
import * as sfx from "./sound.js";

export { cuesOn, setCues } from "./sound.js";

const RENDERED = ["rendered", "approved", "exported"];
const LEAD = 0.7; // s before the first stage
const STAGE_BUDGET = 16.5; // s for all stages
const RESULT_HOLD = 4.5; // s the result card stays

let panel, chip, ticker, winTag;
let mode = null; // null | "live" | "replay"
let raf = 0;
let cards = []; // {id, title, overall, at (ms), el, done}
let result = null; // {el, until}
let live = null; // {jobId, startedAt}
let rp = null; // replay state
let liveWords = { supported: true, last: 0, words: [] };

const now = () => performance.now();
const ts = (v) => (v == null ? NaN : Date.parse(v));
const rm = () => document.documentElement.classList.contains("rm-motion") || matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------ public
export const showActive = () => mode != null || !!result;

/** A predicate for clips the grid should hold back (they're still "in the show"), or null. */
export function hiddenIds() {
  if (mode === "replay" && rp) return (c) => !rp.visible.has(c.id);
  if (mode === "live" && live) return (c) => c.job_id === live.jobId && !RENDERED.includes(c.status);
  return null;
}

/** Latest finished run of this source that produced rendered clips — the one Replay can show. */
export function canReplay() {
  const s = get();
  if (!s.sourceId) return { ok: false, why: "Choose a video first" };
  if (activeJob()) return { ok: false, why: "A run is in progress" };
  const done = sourceJobs().filter((j) => j.status === "done");
  for (const j of done) {
    const st = normStages(j);
    const timed = st.filter((x) => x.started_at && x.ended_at).length;
    const has = s.clips.some((c) => c.job_id === j.id && !c.variant_of && c.status !== "rejected");
    if (has && timed >= 5) return { ok: true, job: j };
  }
  return { ok: false, why: "No finished run with clips on this video yet" };
}

export function toggleClean(force) {
  const on_ = force ?? !document.body.classList.contains("clean");
  document.body.classList.toggle("clean", on_);
  try { localStorage.setItem("clipforge.v2.clean", on_ ? "1" : "0"); } catch {}
  notify({ tone: "ok", title: on_ ? "Clean mode — press F to exit" : "Clean mode off", ms: 1600 });
}

export function mountShow(els) {
  ({ panel, chip, ticker, winTag } = els);
  try { if (localStorage.getItem("clipforge.v2.clean") === "1") document.body.classList.add("clean"); } catch {}
  on(["jobs", "clips", "source", "sourceId", "timeline"], syncLive);
  syncLive();
}

// ------------------------------------------------------------ rendering helpers
function paintPanel(label, pct, frac) {
  let run = panel.querySelector(".show-run");
  if (!run) {
    panel.insertAdjacentHTML("afterbegin", `<div class="show-run"><div class="prog-l"><b></b><span class="mono"></span></div><div class="prog-bar"><i></i></div></div><div class="moments"></div>`);
    run = panel.querySelector(".show-run");
  }
  run.querySelector("b").textContent = label;
  run.querySelector(".mono").textContent = pct != null ? pct + "%" : "";
  run.querySelector("i").style.transform = `scaleX(${Math.max(0.015, Math.min(1, frac))})`;
}
function paintChip(text) {
  chip.hidden = !text;
  if (text) chip.querySelector("span").textContent = text;
}
function addCard(c, at) {
  if (!panel.querySelector(".moments")) paintPanel("Starting…", null, 0.01);
  const box = panel.querySelector(".moments");
  if (!box || cards.some((k) => k.id === c.id)) return;
  const el = document.createElement("div");
  el.className = "mcard";
  el.innerHTML = `<span class="mc-score mono">0</span><span class="mc-t"><b>${esc(displayTitle(c))}</b><small>${tc(c.start_s)} · ${Math.round((c.end_s - c.start_s) || 0)}s</small></span><span class="mc-state"></span>`;
  el.style.visibility = "hidden";
  box.appendChild(el);
  cards.push({ id: c.id, overall: Math.round(c.overall || 0), at, el, popped: false, done: false });
}
function landCard(id) {
  const k = cards.find((x) => x.id === id);
  if (!k || k.done) return;
  k.done = true;
  k.el.classList.add("done");
  k.el.querySelector(".mc-state").innerHTML = `<svg viewBox="0 0 16 16"><path d="M3.5 8.4l3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  sfx.whoosh();
  // its icon is now in the grid: the card folds away so the grid stays the hero
  setTimeout(() => k.el.classList.add("gone"), rm() ? 0 : 650);
}
function stepCards(t) {
  for (const k of cards) {
    if (t < k.at) continue;
    if (!k.popped) {
      k.popped = true;
      k.el.style.visibility = "";
      k.el.classList.add("popin");
      sfx.tick();
    }
    const p = rm() ? 1 : Math.min(1, (t - k.at) / 900);
    const e = 1 - Math.pow(1 - p, 3);
    k.el.querySelector(".mc-score").textContent = String(Math.round(e * k.overall));
  }
}
function clearPanel() {
  panel.innerHTML = "";
  cards = [];
}
function showResult(n, best, seconds) {
  const el = document.createElement("div");
  el.className = "result-card";
  el.innerHTML = `<b>${n} clip${n === 1 ? "" : "s"}</b><i></i><span>best <b class="mono">${best ?? "–"}</b></span><i></i><span class="mono">${tc(seconds)}</span>`;
  panel.parentElement.appendChild(el);
  result = { el, until: now() + RESULT_HOLD * 1000 };
  sfx.chime();
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => {
      el.remove();
      if (result?.el === el) result = null;
      set({ showTick: Date.now() });
    }, 600);
  }, RESULT_HOLD * 1000);
}

// ------------------------------------------------------------ LIVE
function syncLive() {
  if (mode === "replay") return;
  const s = get();
  const job = activeJob();
  if (job) {
    if (!live || live.jobId !== job.id) {
      clearPanel();
      live = { jobId: job.id, startedAt: ts(job.created_at), sourceId: s.sourceId };
      mode = "live";
      set({ showTick: Date.now() });
      loop();
      playSource();
    }
    // moments: this job's clip rows (they exist once detect/score have run)
    const mine = s.clips.filter((c) => c.job_id === job.id && !c.variant_of && c.status !== "rejected" && !c.duplicate_of)
      .sort((a, b) => (b.overall || 0) - (a.overall || 0));
    const t = now();
    mine.forEach((c, i) => addCard(c, t + i * 220));
    mine.filter((c) => RENDERED.includes(c.status)).forEach((c) => landCard(c.id));
    return;
  }
  if (mode === "live" && live) finishLive(live.jobId);
}

async function finishLive(jobId) {
  mode = null;
  cancelAnimationFrame(raf);
  phone2.scan?.(null);
  paintChip(null);
  ticker.hidden = true;
  live = null;
  let job = null;
  try { job = await api.job(jobId); } catch {}
  const clips = get().clips.filter((c) => c.job_id === jobId && !c.variant_of && RENDERED.includes(c.status));
  clearPanel();
  set({ showTick: Date.now() });
  if (job && job.status === "done") {
    const end = Math.max(...normStages(job).map((x) => ts(x.ended_at)).filter(isFinite));
    showResult(clips.length, clips.length ? Math.max(...clips.map((c) => Math.round(c.overall || 0))) : null, (end - ts(job.created_at)) / 1000);
  }
}

async function pollWords(job) {
  if (!liveWords.supported || now() - liveWords.last < 1500) return;
  liveWords.last = now();
  try {
    const r = await api.jobLive(job.id);
    const ws = (r?.words_so_far || []).map((w) => (typeof w === "string" ? w : w.word || w.text || "")).filter(Boolean);
    liveWords.words = ws.slice(-30);
    ticker.hidden = !ws.length;
    ticker.textContent = liveWords.words.join(" ");
  } catch (e) {
    if (e.status === 404 || e.status === 405) liveWords.supported = false; // engine doesn't expose it yet: stage text only
    ticker.hidden = true;
  }
}

// ------------------------------------------------------------ REPLAY
export async function startReplay() {
  if (mode === "replay") return stopReplay(true);
  const r = canReplay();
  if (!r.ok) return notify({ tone: "info", title: "Nothing to replay", detail: r.why });
  try {
    const job = await api.job(r.job.id);
    const clips = (await api.clips({ job_id: job.id, status: "all" })) || [];
    const events = [];
    for (let since = 0, page = 0; page < 8; page++) {
      const ev = (await api.events(since)) || [];
      events.push(...ev);
      if (ev.length < 500) break;
      since = ev[ev.length - 1].id;
    }
    rp = buildReplay(job, clips, events);
  } catch (e) {
    return fail(e, "Couldn't load the run to replay");
  }
  clearPanel();
  mode = "replay";
  rp.t0 = now();
  set({ selection: new Set() });
  selectClip(null);
  winTag.hidden = false;
  winTag.textContent = `Replay · ${tc(rp.realSeconds)} → ${Math.round(rp.total)}s`;
  rp.moments.forEach((m) => addCard(m.clip, rp.t0 + m.at * 1000));
  playSource();
  set({ showTick: Date.now() });
  loop();
}

export function stopReplay(user) {
  if (mode !== "replay") return;
  mode = null;
  cancelAnimationFrame(raf);
  winTag.hidden = true;
  phone2.scan?.(null);
  phone2.setTag?.(null);
  paintChip(null);
  clearPanel();
  const best = rp?.moments[0]?.clip;
  rp = null;
  set({ showTick: Date.now() });
  if (user && best) selectClip(best.id);
}

function buildReplay(job, clips, events) {
  const st = normStages(job);
  const t0 = ts(job.created_at);
  // real durations → replay segments (sqrt weighting so short stages stay visible; order and relative size kept)
  const real = st.map((x) => {
    const a = ts(x.started_at), b = ts(x.ended_at);
    return isFinite(a) && isFinite(b) ? Math.max(0, (b - a) / 1000) : 0;
  });
  const w = real.map((d) => (d > 0.05 ? Math.sqrt(d) + 0.35 : 0.15));
  const W = w.reduce((a, b) => a + b, 0);
  let acc = LEAD;
  const segs = st.map((x, i) => {
    const seg = { name: x.name, status: x.status, a: acc, b: acc + (w[i] / W) * STAGE_BUDGET, ra: ts(x.started_at), rb: ts(x.ended_at) };
    acc = seg.b;
    return seg;
  });
  const end = acc;
  const realEnd = Math.max(...segs.map((x) => x.rb).filter(isFinite));
  // moments = this run's real clips that cleared the bar, best first; they pop when scoring finished
  const found = clips.filter((c) => !c.variant_of && c.status !== "rejected" && !c.duplicate_of).sort((a, b) => (b.overall || 0) - (a.overall || 0));
  const scoreSeg = segs.find((x) => x.name === "score") || segs.find((x) => x.name === "detect");
  const moments = found.map((c, i) => ({ clip: c, at: scoreSeg.b - 0.25 + i * 0.24 }));
  // landings = each clip's first recorded "Clip ready" event in this run, placed inside its real stage
  const ready = new Map();
  for (const e of events) {
    const id = e.ref?.clip_id;
    const t = ts(e.ts);
    if (e.kind !== "clip" || id == null || !/ready/i.test(e.message || "") || ready.has(id)) continue;
    if (t >= t0 && t <= realEnd + 5000) ready.set(id, t);
  }
  const place = (t) => {
    const seg = segs.find((x) => isFinite(x.ra) && isFinite(x.rb) && t >= x.ra && t <= x.rb + 1) || segs.find((x) => x.name === "render");
    if (!isFinite(seg.ra) || !isFinite(seg.rb) || seg.rb <= seg.ra) return seg.b;
    return seg.a + ((Math.min(t, seg.rb) - seg.ra) / (seg.rb - seg.ra)) * (seg.b - seg.a);
  };
  const renderSeg = segs.find((x) => x.name === "render");
  const landings = found.map((c) => {
    const t = ready.get(c.id);
    // no recorded event → it was certainly ready by the end of the render stage (real), so it lands there
    return { id: c.id, at: Math.max(scoreSeg.b + 0.4, isFinite(t) ? place(t) : renderSeg.b) };
  }).sort((a, b) => a.at - b.at);
  // keep landings readable: at least 0.45 s apart, never past the end
  for (let i = 1; i < landings.length; i++) landings[i].at = Math.max(landings[i].at, landings[i - 1].at + 0.45);
  const total = Math.max(end, landings.length ? landings[landings.length - 1].at + 0.4 : end);
  return {
    job, segs, moments, landings, total, visible: new Set(),
    realSeconds: (realEnd - t0) / 1000,
    best: found.length ? Math.round(found[0].overall || 0) : null,
    count: found.filter((c) => RENDERED.includes(c.status)).length || found.length,
  };
}

/** the phone plays the source, muted, while the show runs (it's already loaded once no clip is selected) */
function playSource() {
  const tryPlay = (n) => {
    const v = phone2.video;
    if (!v) return;
    if (!v.src) return n > 0 && setTimeout(() => tryPlay(n - 1), 250);
    v.muted = true;
    v.play().catch(() => {});
  };
  setTimeout(() => tryPlay(8), 200);
}

// ------------------------------------------------------------ frame loop (only while a show runs)
function loop() {
  cancelAnimationFrame(raf);
  const frame = () => {
    if (mode === "replay") frameReplay();
    else if (mode === "live") frameLive();
    else return;
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
}

function frameLive() {
  const job = activeJob();
  if (!job) return;
  const st = normStages(job);
  const cur = st.find((x) => x.status === "running") || st.find((x) => x.status === "pending");
  const label = job.status === "queued" ? "Queued" : STAGE_LABEL[cur?.name] || cur?.name || "Working";
  const pct = cur && cur.progress > 0 ? Math.round(cur.progress * 100) : null;
  const frac = pct != null ? cur.progress : job.progress ?? 0;
  paintPanel(label + "…", pct, frac);
  paintChip(`Processing · ${label}${pct != null ? " " + pct + "%" : ""}`);
  // scan line: real transcription coverage while it's running
  const tr = st.find((x) => x.name === "transcribe");
  if (tr?.status === "running") {
    const cov = get().timeline?.words_coverage;
    phone2.scan?.(cov != null && cov > 0 ? cov : tr.progress || 0);
    pollWords(job);
  } else phone2.scan?.(null);
  stepCards(now());
}

function frameReplay() {
  const t = (now() - rp.t0) / 1000;
  const seg = rp.segs.find((x) => t >= x.a && t < x.b) || (t < LEAD ? null : rp.segs[rp.segs.length - 1]);
  if (t >= rp.total) return endReplay();
  if (seg) {
    const p = Math.max(0, Math.min(1, (t - seg.a) / (seg.b - seg.a)));
    const label = STAGE_LABEL[seg.name] || seg.name;
    paintPanel(label + "…", Math.round(p * 100), p); // one number: the bar is the same % as the label
    paintChip(`Replay · ${label}`);
    phone2.setTag?.(`Replay · ${label}`);
    const tr = rp.segs.find((x) => x.name === "transcribe");
    phone2.scan?.(t < tr.a ? 0 : t > tr.b ? null : (t - tr.a) / (tr.b - tr.a));
  } else {
    paintPanel("Starting…", null, 0.01);
    paintChip("Replay");
    phone2.setTag?.("Replay");
  }
  stepCards(now());
  let landed = false;
  for (const l of rp.landings) {
    if (t >= l.at && !rp.visible.has(l.id)) {
      rp.visible.add(l.id);
      landCard(l.id);
      landed = true;
    }
  }
  if (landed) set({ showTick: Date.now() });
}

function endReplay() {
  const r = rp;
  mode = null;
  cancelAnimationFrame(raf);
  phone2.scan?.(null);
  phone2.setTag?.(null);
  paintChip(null);
  clearPanel();
  rp = null;
  set({ showTick: Date.now() });
  showResult(r.landings.length, r.best, r.realSeconds);
  setTimeout(() => (winTag.hidden = true), RESULT_HOLD * 1000);
  const best = r.moments[0]?.clip;
  if (best) selectClip(best.id);
}

export const replaying = () => mode === "replay";
