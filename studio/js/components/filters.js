// Filter row above the clip browser + the shared filter predicate.
import { get, set, on, persist } from "../store.js";
import { esc, icon, isBelowBar } from "../util.js";
import { loadClips } from "../actions.js";

export const BANDS = [
  ["", "Any length"],
  ["0-30", "Under 30s"],
  ["30-60", "30–60s"],
  ["60-90", "60–90s"],
  ["90-", "Over 90s"],
];
export const STATUS_FILTERS = [
  ["active", "Active"],
  ["rendered", "Ready"],
  ["approved", "Approved"],
  ["exported", "Exported"],
  ["failed", "Failed"],
  ["below", "Below bar"],
  ["rejected", "Rejected"],
  ["all", "All"],
];
const SCORES = [0, 50, 60, 70, 80, 90];
const SORTS = [
  ["score", "Best moments"],
  ["time", "In order"],
  ["duration", "Shortest"],
];

const clipDur = (c) => c.duration ?? (c.end_s ?? 0) - (c.start_s ?? 0);

export function applyFilters(clips, f = get().filters, sort = get().sort) {
  let out = clips.filter((c) => {
    if (f.creator && String(c.creator_id ?? c.creator_name) !== String(f.creator) && c.creator_name !== f.creator) return false;
    const st = f.status || "active";
    if (st === "active" && (c.status === "rejected" || c.duplicate_of)) return false;
    if (st === "below" && !isBelowBar(c)) return false;
    if (st === "rejected" && (c.status !== "rejected" || isBelowBar(c))) return false;
    if (!["active", "all", "below", "rejected"].includes(st) && c.status !== st) return false;
    if (f.minScore && (c.overall || 0) < f.minScore) return false;
    if (f.q) {
      const q = String(f.q).toLowerCase();
      if (![c.title, c.description, c.transcript_excerpt].some((x) => String(x || "").toLowerCase().includes(q))) return false;
    }
    if (f.band) {
      const [a, b] = f.band.split("-").map((x) => (x === "" ? null : Number(x)));
      const d = clipDur(c);
      if (a != null && d < a) return false;
      if (b != null && d > b) return false;
    }
    return true;
  });
  if (sort === "score") out.sort((a, b) => (b.overall || 0) - (a.overall || 0) || (a.rank || 0) - (b.rank || 0));
  if (sort === "time") out.sort((a, b) => String(a.source_id).localeCompare(String(b.source_id)) || a.start_s - b.start_s);
  if (sort === "duration") out.sort((a, b) => clipDur(a) - clipDur(b));
  return out;
}

export function mountFilters(root) {
  root.classList.add("filters");
  let open = false;
  function render() {
    const s = get();
    const f = s.filters;
    const creators = [...new Map(s.clips.map((c) => [c.creator_id ?? c.creator_name, c.creator_name || "—"])).entries()];
    const vis = applyFilters(s.clips);
    const active = [f.band, f.minScore, f.status && f.status !== "active" ? 1 : 0, f.scope === "all" && f.creator].filter(Boolean).length;
    root.classList.toggle("open", open);
    root.innerHTML = `
      <div class="seg sm" role="radiogroup" aria-label="Scope">
        <button type="button" role="radio" data-scope="source" aria-checked="${f.scope !== "all"}">This<span class="nar"> source</span></button>
        <button type="button" role="radio" data-scope="all" aria-checked="${f.scope === "all"}">All<span class="nar"> sources</span></button>
      </div>
      <button type="button" class="btn ghost sm f-more" data-fmore aria-expanded="${open}">Filter${active ? ` <span class="mono">${active}</span>` : ""}</button>
      <span class="f-sets">
      ${f.scope === "all" && creators.length > 1 ? sel("creator", [["", "All creators"], ...creators.map(([k, n]) => [String(k), n])], f.creator || "") : ""}
      ${sel("band", BANDS, f.band || "")}
      ${sel("minScore", SCORES.map((n) => [String(n), n ? "Score ≥ " + n : "Any score"]), String(f.minScore || 0))}
      ${sel("status", STATUS_FILTERS, f.status || "active")}
      </span>
      <span class="f-gap"></span>
      <span class="f-count mono">${vis.length}<span> / ${s.clips.length}</span></span>
      ${sel("sort", SORTS, s.sort)}
      <div class="seg icons" role="radiogroup" aria-label="View">
        <button type="button" role="radio" data-view="grid" aria-checked="${s.view === "grid"}" aria-label="Grid" title="Grid">${icon.grid}</button>
        <button type="button" role="radio" data-view="list" aria-checked="${s.view === "list"}" aria-label="List" title="List">${icon.list}</button>
      </div>`;
  }
  function sel(name, opts, val) {
    return `<div class="select sm ghost"><select data-f="${name}" aria-label="${name}">${opts
      .map(([k, l]) => `<option value="${esc(k)}" ${String(k) === String(val) ? "selected" : ""}>${esc(l)}</option>`)
      .join("")}</select>${icon.chev}</div>`;
  }
  root.addEventListener("change", (e) => {
    const k = e.target.dataset.f;
    if (!k) return;
    if (k === "sort") {
      set({ sort: e.target.value });
      persist("sort", e.target.value);
      return;
    }
    const f = { ...get().filters, [k]: k === "minScore" ? Number(e.target.value) : e.target.value };
    set({ filters: f });
    persist("filters", f);
  });
  root.addEventListener("click", (e) => {
    const sc = e.target.closest("[data-scope]");
    if (sc) {
      const f = { ...get().filters, scope: sc.dataset.scope, creator: "" };
      set({ filters: f });
      persist("filters", f);
      loadClips();
    }
    if (e.target.closest("[data-fmore]")) {
      open = !open;
      render();
      return;
    }
    const v = e.target.closest("[data-view]");
    if (v) {
      set({ view: v.dataset.view });
      persist("view", v.dataset.view);
    }
  });
  const off = on(["clips", "filters", "sort", "view"], render);
  render();
  return off;
}
