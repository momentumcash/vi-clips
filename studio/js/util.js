// Small helpers shared by every component.

export const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** 1:02:03 / 4:05 — source-scale timecode */
export function tc(s) {
  if (s == null || !isFinite(s)) return "–:––";
  s = Math.max(0, s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
}

/** 0:04.3 — clip-scale timecode with tenths */
export function tcf(s) {
  if (s == null || !isFinite(s)) return "–:––.–";
  s = Math.max(0, s);
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return m + ":" + sec.toFixed(1).padStart(4, "0");
}

/** 37s / 1m 12s */
export function dur(s) {
  if (s == null || !isFinite(s)) return "–";
  if (s < 60) return Math.round(s) + "s";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function ago(ts) {
  if (!ts) return "";
  const t = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
  if (!isFinite(t)) return "";
  const d = (Date.now() - t) / 1000;
  if (d < 45) return "just now";
  if (d < 3600) return Math.round(d / 60) + "m ago";
  if (d < 86400) return Math.round(d / 3600) + "h ago";
  return Math.round(d / 86400) + "d ago";
}

export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

export function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

/** Build an element from an HTML string (single root). */
export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export const RIGHTS_OK = ["owned", "licensed", "permission_granted"];

export const RIGHTS_LABEL = {
  owned: "Owned",
  licensed: "Licensed",
  permission_granted: "Permission granted",
  pending: "Pending",
  unknown: "Unknown",
  denied: "Denied",
};

export const RIGHTS_EXPLAIN = {
  owned: "It's your own footage. Export is allowed.",
  licensed: "You hold a licence for this creator's footage. Export is allowed.",
  permission_granted: "The creator said yes in writing. Export is allowed — keep that message.",
  pending: "You've asked, no answer yet. Clips can be made and reviewed, not exported.",
  unknown: "Nobody has checked. Clips can be made and reviewed, not exported.",
  denied: "The creator said no. Clips can be reviewed privately, never exported.",
};

export function rightsTone(r) {
  if (RIGHTS_OK.includes(r)) return "ok";
  if (r === "denied") return "bad";
  return "warn";
}

export const PRESETS = [
  ["clean_podcast", "Clean podcast"],
  ["gaming", "Gaming"],
  ["bold_entertainment", "Bold"],
  ["minimal_cinematic", "Minimal"],
  ["high_energy", "High energy"],
];
export const presetLabel = (k) => (PRESETS.find((p) => p[0] === k) || [k, k || "—"])[1];

export const TARGETS = [
  ["auto", "Auto"],
  ["15-30", "15–30"],
  ["30-60", "30–60"],
  ["60-90", "60–90"],
];

/** Score keys in weight order (contract). */
export const SCORE_KEYS = [
  ["hook", "Hook", 0.18],
  ["standalone", "Standalone", 0.14],
  ["entertainment", "Entertainment", 0.12],
  ["emotion", "Emotion", 0.1],
  ["curiosity", "Curiosity", 0.1],
  ["pacing", "Pacing", 0.08],
  ["clarity", "Clarity", 0.08],
  ["replay", "Replay", 0.07],
  ["share", "Share", 0.07],
  ["open_frame", "Open frame", 0.06],
];

export const STAGES = ["verify", "probe", "extract_audio", "transcribe", "signals", "detect", "score", "dedupe", "render", "qc"];
export const STAGE_LABEL = {
  verify: "Verify",
  probe: "Probe",
  extract_audio: "Audio",
  transcribe: "Transcribe",
  signals: "Signals",
  detect: "Find moments",
  score: "Score",
  dedupe: "Dedupe",
  render: "Render",
  qc: "QC",
};

export const STATUS_LABEL = {
  candidate: "Candidate",
  rendering: "Rendering",
  rendered: "Ready",
  failed: "Failed",
  approved: "Approved",
  rejected: "Rejected",
  exported: "Exported",
};

export function statusTone(s) {
  return (
    {
      candidate: "idle",
      rendering: "live",
      rendered: "ready",
      failed: "bad",
      approved: "ok",
      rejected: "off",
      exported: "done",
    }[s] || "idle"
  );
}

export function copyText(t) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(t).catch(() => fallbackCopy(t));
  fallbackCopy(t);
  return Promise.resolve();
}
function fallbackCopy(t) {
  const ta = document.createElement("textarea");
  ta.value = t;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch {}
  ta.remove();
}

export const icon = {
  play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 2.8v10.4L13 8z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h2.6v11H4zM9.4 2.5H12v11H9.4z" fill="currentColor"/></svg>',
  grid: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
  list: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4h11M2.5 8h11M2.5 12h11" stroke="currentColor" stroke-width="1.2"/></svg>',
  check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 8.4l3 3 6.6-6.8" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
  x: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4"/></svg>',
  chev: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.2L8 9.7l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  mute: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6h2.4L8.5 3v10L4.9 10H2.5z" fill="currentColor"/><path d="M11 6l3.5 4M14.5 6L11 10" stroke="currentColor" stroke-width="1.2"/></svg>',
  sound: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6h2.4L8.5 3v10L4.9 10H2.5z" fill="currentColor"/><path d="M10.8 5.6a3.4 3.4 0 010 4.8M12.6 3.8a6 6 0 010 8.4" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
  copy: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8.5" height="8.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M3 10.5V3h7.5" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
};

/** Nearest real source frame (from the engine's spine filmstrip) for a clip that has no render yet. */
export function sourceFrame(clip, timeline) {
  const fs = timeline?.frames;
  if (!clip || !fs?.length) return "";
  if (timeline.source_id != null && String(timeline.source_id) !== String(clip.source_id)) return "";
  const t = clip.best_open_s ?? clip.start_s ?? 0;
  let best = fs[0];
  for (const f of fs) if (Math.abs(f.t - t) < Math.abs(best.t - t)) best = f;
  return best.url;
}

/** Engine reject reasons → plain words. */
export function rejectText(r) {
  if (!r) return "";
  const m = String(r).match(/score\s+(\d+(?:\.\d+)?)\s+below\s+min_score\s+(\d+(?:\.\d+)?)/i);
  if (m) return `Scored ${Math.round(+m[1])} — under the ${Math.round(+m[2])} bar, so not rendered.`;
  return String(r).charAt(0).toUpperCase() + String(r).slice(1);
}

/** Title without the engine's "(alt open N)" suffix — the variant pill carries that. */
export const displayTitle = (c) => String(c?.title || "Untitled moment").replace(/\s*\(alt open \d+\)\s*$/i, "");
/** Variant group root id. */
export const rootOf = (c) => c?.variant_of ?? c?.id;

/** Below the score bar: the engine's flag when present, else a rejected candidate whose reason is the score bar. */
export const isBelowBar = (c) =>
  c?.below_bar != null ? !!c.below_bar : c?.status === "rejected" && /below\s+min_score/i.test(c?.reject_reason || "");

/** Human line for an engine error; the raw text goes behind "Details". */
export function humanError(err, stageLabel) {
  const e = String(err || "").trim();
  if (!e) return stageLabel ? `Stopped at ${stageLabel}.` : "Stopped.";
  if (/^[A-Z]\w*(Error|Exception)\b/.test(e) || /Traceback|module '|attribute '/.test(e))
    return `Engine error${stageLabel ? " in " + stageLabel : ""}.`;
  const first = e.split(" — ")[0].replace(/^\w+:\s*/, (m) => (/^(verify|probe|extract_audio|transcribe|signals|detect|score|dedupe|render|qc):/.test(m) ? "" : m));
  return first.charAt(0).toUpperCase() + first.slice(1) + (/[.!?]$/.test(first) ? "" : ".");
}

/** Engine activity copy → plain words. */
export function prettyEvent(m) {
  return String(m || "")
    .replace(/^Done:\s*(\d+)\s*clip\(s\)\s*ready/i, (_, n) => `${n} clip${n === "1" ? "" : "s"} ready`)
    .replace(/(\d+)\s+clip\(s\)/g, (_, n) => `${n} clip${n === "1" ? "" : "s"}`)
    .replace(/score\s+(\d+)\.0\b/g, "score $1")
    .replace(/\s*·\s*LLM \$[\d.]+/, "");
}

/** Spoken words in [a, b] (absolute seconds) from a words list. */
export function wordsBetween(words, a, b) {
  return (words || [])
    .map((w) => ({ s: w.start ?? w.s, e: w.end ?? w.e, t: (w.word ?? w.text ?? "").trim() }))
    .filter((w) => w.t && w.e > a + 0.01 && w.s < b - 0.01);
}

/**
 * One entry per real moment. Shared by the browser, the spine and the overview.
 * - openings: hook variants (`variant_of`) fold under their parent take.
 * - takes: clips from the same source (re-runs) whose ranges overlap ≥ 60% of the shorter one fold together.
 * Takes are ordered best first: not rejected, then highest score, then newest run.
 * Moments keep the order of their first member in `list` (so a score-sorted list stays score-sorted).
 */
export function momentsOf(list) {
  const roots = new Map(); // root id → { root, openings[] }
  const order = [];
  for (const c of list) {
    const r = rootOf(c);
    if (!roots.has(r)) {
      roots.set(r, { id: r, openings: [] });
      order.push(r);
    }
    roots.get(r).openings.push(c);
  }
  const takes = order.map((r) => {
    const t = roots.get(r);
    t.openings.sort((a, b) => (a.variant_of ? 1 : 0) - (b.variant_of ? 1 : 0) || a.id - b.id);
    t.root = t.openings[0];
    return t;
  });
  // union-find over takes of the same source by range overlap
  const parent = takes.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const ov = (a, b) => {
    if (String(a.source_id) !== String(b.source_id)) return 0;
    const inter = Math.min(a.end_s, b.end_s) - Math.max(a.start_s, b.start_s);
    const shorter = Math.min(a.end_s - a.start_s, b.end_s - b.start_s);
    return inter > 0 && shorter > 0 ? inter / shorter : 0;
  };
  for (let i = 0; i < takes.length; i++)
    for (let j = i + 1; j < takes.length; j++) if (ov(takes[i].root, takes[j].root) >= 0.6) parent[find(j)] = find(i);
  const groups = new Map();
  const gOrder = [];
  takes.forEach((t, i) => {
    const k = find(i);
    if (!groups.has(k)) {
      groups.set(k, []);
      gOrder.push(k);
    }
    groups.get(k).push(t);
  });
  const rank = (c) => [c.status === "rejected" ? 1 : 0, -(c.overall || 0), -(c.job_id || 0), -(c.id || 0)];
  const cmp = (a, b) => {
    const x = rank(a.root);
    const y = rank(b.root);
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return 0;
  };
  return gOrder.map((k) => {
    const ts = groups.get(k).sort(cmp);
    return { key: ts[0].root.id, takes: ts, best: ts[0].root, all: ts.flatMap((t) => t.openings) };
  });
}
