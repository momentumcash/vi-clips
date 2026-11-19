// Tiny state store. Components subscribe to the keys they care about; nothing else couples them.

const LS = "clipforge.ui.";
const saved = (k, d) => {
  try {
    const v = localStorage.getItem(LS + k);
    return v == null ? d : JSON.parse(v);
  } catch {
    return d;
  }
};
export const persist = (k, v) => {
  try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch {}
};

const state = {
  route: "studio",
  engine: { online: null, version: "", root: "" }, // null = checking
  live: false, // SSE connected

  creators: [],
  sources: [],
  sourcesLoaded: false,
  sourceId: saved("sourceId", null),
  source: null, // detail: source + jobs + clip counts
  timeline: null, // {duration, energy, frames, words_coverage} or null when the engine has none
  jobs: [],

  clips: [],
  clipsLoaded: false,
  clipId: null,
  clip: null, // detail incl. words
  selection: new Set(),
  view: saved("view", "grid"),
  sort: saved("sort", "score"),
  filters: saved("filters", { creator: "", status: "active", band: "", minScore: 0 }),
  inspector: saved("inspector", true),
  why: null, // clip id whose "why" is revealed

  target: saved("target", "auto"),
  preset: saved("preset", "bold_entertainment"),

  overview: null,
  integrations: [],
  settings: null,
  lastExport: null,
  notice: null, // {tone, title, detail, fix, path, action}
  playhead: null, // source-time of the phone playhead
  hoverClip: null,
};

const subs = new Set();

export function get() {
  return state;
}

export function set(patch) {
  const changed = [];
  for (const k of Object.keys(patch)) {
    if (state[k] !== patch[k]) {
      state[k] = patch[k];
      changed.push(k);
    }
  }
  if (!changed.length) return;
  for (const s of subs) {
    if (!s.keys || s.keys.some((k) => changed.includes(k))) {
      try { s.fn(state, changed); } catch (e) { console.error(e); }
    }
  }
}

/** on(["clips","clipId"], fn) → unsubscribe */
export function on(keys, fn) {
  const s = { keys, fn };
  subs.add(s);
  return () => subs.delete(s);
}
