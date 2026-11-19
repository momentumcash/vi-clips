// Clip browser: grid / list of real clips, multi-select, "why" reveal, sticky selection bar.
import { get, set, on } from "../store.js";
import { esc, tc, dur, icon, SCORE_KEYS, STATUS_LABEL, statusTone, sourceFrame, rejectText, displayTitle, rootOf, isBelowBar, momentsOf } from "../util.js";
import { selectClip, setStatus, exportClips } from "../actions.js";
import { applyFilters } from "./filters.js";

let anchor = null;

function thumbHTML(c, s) {
  if (c.thumb_url) return `<img src="${esc(c.thumb_url)}" alt="" loading="lazy" draggable="false">`;
  const f = sourceFrame(c, s.timeline);
  const tag = c.status === "rendering" ? "Rendering" : "Not rendered";
  if (f) return `<img class="raw" src="${esc(f)}" alt="" loading="lazy" draggable="false"><span class="ctag">${tag}</span>`;
  return `<span class="nothumb">${tag}</span>`;
}

/** Filtered clips folded into moments (re-run takes + hook openings): one entry per moment.
 *  Moments are defined on ALL loaded clips (same as the spine), then filtered: a moment shows when any member passes.
 *  `_members` = passing openings of the shown take, `_takes` = passing take roots, best first. */
export function grouped(list = applyFilters(get().clips)) {
  const s = get();
  const pass = new Set(list.map((c) => c.id));
  const byClip = new Map();
  for (const m of momentsOf(s.clips)) for (const c of m.all) byClip.set(c.id, m);
  const out = [];
  const seen = new Set();
  for (const c of list) {
    const m = byClip.get(c.id);
    if (!m || seen.has(m)) continue;
    seen.add(m);
    const takes = m.takes
      .map((t) => ({ ...t, openings: t.openings.filter((o) => pass.has(o.id)) }))
      .filter((t) => t.openings.length);
    const take = takes.find((t) => t.openings.some((o) => o.id === s.clipId)) || takes[0];
    const shown = take.openings.find((o) => o.id === s.clipId) || take.openings[0];
    out.push({ ...shown, _members: take.openings, _takes: takes.map((t) => t.openings[0]), _all: m.all });
  }
  return out;
}

export function visibleClips() {
  return grouped();
}

export function mountBrowser(root, selbar) {
  root.classList.add("browser");
  root.tabIndex = -1;

  function render() {
    const s = get();
    const list = grouped();
    root.dataset.view = s.view;
    if (!list.length) {
      const live = s.clips.filter((c) => c.status !== "rejected" && !c.duplicate_of);
      const below = s.clips.filter(isBelowBar).length;
      const allBelow = s.clips.length && !live.length && (s.filters.status || "active") === "active";
      root.innerHTML = allBelow
        ? `<div class="b-empty"><p class="st-big">Nothing worth posting in this one.</p>${
            below ? `<button class="btn sm" type="button" data-show-below>Show ${below} below-bar moment${below === 1 ? "" : "s"}</button>` : ""
          }</div>`
        : s.clips.length
        ? `<div class="b-empty"><p>No clips match these filters.</p><button class="btn sm" type="button" data-clear>Clear filters</button></div>`
        : "";
      root.classList.toggle("is-empty", true);
      renderSel();
      return;
    }
    root.classList.remove("is-empty");
    root.innerHTML =
      s.view === "list"
        ? `<div class="lrow lhead" aria-hidden="true"><span></span><span></span><span>Clip</span><span>In source</span><span>Length</span><span>Score</span><span>Status</span></div>` +
          list.map((c) => row(c, s)).join("")
        : `<div class="grid">${list.map((c) => card(c, s)).join("")}</div>`;
    renderSel();
    const on_ = root.querySelector(".on");
    if (on_ && s._scrollTo === s.clipId) on_.scrollIntoView({ block: "nearest" });
  }

  function card(c, s) {
    const sel = s.selection.has(c.id);
    const on_ = s.clipId === c.id;
    const why = s.why === c.id;
    const st = c.status;
    return `
      <article class="card ${on_ ? "on" : ""} ${sel ? "picked" : ""} st-${esc(st)}" data-id="${esc(c.id)}" tabindex="0" aria-selected="${on_}">
        <div class="cthumb">
          ${thumbHTML(c, s)}
          <button class="cbox" type="button" role="checkbox" aria-checked="${sel}" aria-label="Select clip" data-pick>${icon.check}</button>
          <span class="cdur mono">${tc(c.duration ?? c.end_s - c.start_s)}</span>
          ${c._members?.length > 1 ? `<button type="button" class="cvar" data-cyc title="Same moment, ${c._members.length} different openings — click to switch">${c._members.map((m) => `<i class="${m.id === c.id ? "on" : ""}"></i>`).join("")}<span>+${c._members.length - 1} opening${c._members.length > 2 ? "s" : ""}</span></button>` : ""}
          ${c._takes?.length > 1 ? `<button type="button" class="ctake" data-take title="The same moment found in ${c._takes.length} runs — click to switch">+${c._takes.length - 1} take${c._takes.length > 2 ? "s" : ""}</button>` : ""}
          ${why ? whyPanel(c) : ""}
        </div>
        <div class="cmeta">
          <h3 class="ctitle">${esc(displayTitle(c))}</h3>
          <div class="crow">
            <span class="cscore mono" title="ClipForge score — editorial estimate, not a view prediction">${c.overall ?? "–"}</span>
            <span class="cstat" title="${esc(STATUS_LABEL[st] || st)}"><span class="dot t-${statusTone(st)}"></span><span class="st-t">${esc(STATUS_LABEL[st] || st)}</span></span>
            <button class="why-btn" type="button" data-why aria-expanded="${why}">${why ? "Hide" : "Why"}</button>
          </div>
          ${s.filters.scope === "all" ? `<div class="csub">${esc(c.creator_name || "")}</div>` : ""}
        </div>
      </article>`;
  }

  function whyPanel(c) {
    const sc = c.scores || {};
    const rs = c.reasons || {};
    const keys = SCORE_KEYS.filter(([k]) => sc[k] != null)
      .map(([k, l, w]) => ({ k, l, v: sc[k], w }))
      .sort((a, b) => b.v * b.w - a.v * a.w);
    const top = keys.slice(0, 3);
    const low = keys.slice().sort((a, b) => a.v - b.v)[0];
    const line = (x) => `<li><span class="mono">${x.v}</span><b>${esc(x.l)}</b></li>`;
    return `<div class="cwhy" data-whypanel><span class="cwhy-h">Strongest</span><ul>${top.map(line).join("")}</ul>${
      low && !top.includes(low) ? `<span class="cwhy-h">Weakest</span><ul>${line(low)}</ul>` : ""
    }${c.status === "rejected" && c.reject_reason ? `<p>${esc(rejectText(c.reject_reason))}</p>` : ""}</div>`;
  }

  function row(c, s) {
    const sel = s.selection.has(c.id);
    const on_ = s.clipId === c.id;
    return `
      <div class="lrow ${on_ ? "on" : ""} ${sel ? "picked" : ""}" data-id="${esc(c.id)}" tabindex="0" aria-selected="${on_}">
        <button class="cbox" type="button" role="checkbox" aria-checked="${sel}" aria-label="Select clip" data-pick>${icon.check}</button>
        <span class="lthumb">${c.thumb_url ? `<img src="${esc(c.thumb_url)}" alt="" loading="lazy">` : sourceFrame(c, s.timeline) ? `<img class="raw" src="${esc(sourceFrame(c, s.timeline))}" alt="" loading="lazy">` : ""}</span>
        <span class="ltitle"><b>${esc(displayTitle(c))}${c._members?.length > 1 ? ` <span class="cvar-i mono">+${c._members.length - 1}</span>` : ""}${c._takes?.length > 1 ? ` <span class="cvar-i">+${c._takes.length - 1} take${c._takes.length > 2 ? "s" : ""}</span>` : ""}</b><small>${esc(c.description || "")}</small></span>
        <span class="mono lsrc">${tc(c.start_s)}–${tc(c.end_s)}</span>
        <span class="mono llen">${dur(c.duration ?? c.end_s - c.start_s)}</span>
        <span class="mono lscore">${c.overall ?? "–"}</span>
        <span class="cstat"><span class="dot t-${statusTone(c.status)}"></span>${esc(STATUS_LABEL[c.status] || c.status)}</span>
      </div>`;
  }

  function renderSel() {
    const s = get();
    const n = s.selection.size;
    selbar.hidden = !n;
    if (!n) return;
    selbar.innerHTML = `
      <span class="mono"><b>${n}</b> selected</span>
      <span class="sb-gap"></span>
      <button class="icon-btn" type="button" data-bulk="clear" aria-label="Clear selection" title="Clear selection (Esc)">${icon.x}</button>
      <button class="btn sm" type="button" data-bulk="reject">Reject</button>
      <button class="btn sm" type="button" data-bulk="approve">Approve</button>
      <button class="btn primary sm" type="button" data-bulk="export">Export</button>`;
  }

  selbar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    const ids = [...get().selection];
    if (b.dataset.bulk === "clear") set({ selection: new Set() });
    if (b.dataset.bulk === "approve") setStatus(ids, "approved");
    if (b.dataset.bulk === "reject") setStatus(ids, "rejected");
    if (b.dataset.bulk === "export") exportClips(ids);
  });

  root.addEventListener("click", (e) => {
    if (e.target.closest("[data-show-below]")) {
      set({ filters: { ...get().filters, status: "below" } });
      return;
    }
    if (e.target.closest("[data-clear]")) {
      set({ filters: { ...get().filters, band: "", minScore: 0, status: "active", creator: "" } });
      return;
    }
    const item = e.target.closest("[data-id]");
    if (!item) return;
    const id = coerce(item.dataset.id);
    if (e.target.closest("[data-take]")) {
      const g = grouped().find((x) => x.id === id);
      const ts = g?._takes || [];
      const cur = ts.findIndex((t) => t.id === rootOf(g));
      if (ts.length > 1) selectClip(ts[(cur + 1) % ts.length].id);
      return;
    }
    if (e.target.closest("[data-cyc]")) {
      const g = grouped().find((x) => x.id === id);
      const ms = g?._members || [];
      const i = ms.findIndex((m) => m.id === id);
      if (ms.length > 1) selectClip(ms[(i + 1) % ms.length].id);
      return;
    }
    if (e.target.closest("[data-why]")) {
      set({ why: get().why === id ? null : id });
      return;
    }
    if (e.target.closest("[data-whypanel]")) return;
    if (e.target.closest("[data-pick]") || e.metaKey || e.ctrlKey) {
      togglePick(id);
      anchor = id;
      return;
    }
    if (e.shiftKey && anchor != null) {
      rangePick(anchor, id);
      return;
    }
    anchor = id;
    if (get().selection.size) set({ selection: new Set() });
    selectClip(id);
  });
  root.addEventListener("dblclick", (e) => {
    const item = e.target.closest("[data-id]");
    if (item && !e.target.closest("[data-pick],[data-why]")) selectClip(coerce(item.dataset.id), { open: true });
  });
  root.addEventListener("keydown", (e) => {
    const item = e.target.closest("[data-id]");
    if (item && e.key === "Enter") selectClip(coerce(item.dataset.id), { open: true });
  });

  const off = on(["clips", "clipId", "selection", "filters", "sort", "view", "why", "settings", "timeline"], render);
  render();
  return off;
}

function togglePick(id) {
  const sel = new Set(get().selection);
  sel.has(id) ? sel.delete(id) : sel.add(id);
  set({ selection: sel });
}
function rangePick(a, b) {
  const ids = visibleClips().map((c) => c.id);
  const i = ids.indexOf(a);
  const j = ids.indexOf(b);
  if (i < 0 || j < 0) return togglePick(b);
  const sel = new Set(get().selection);
  ids.slice(Math.min(i, j), Math.max(i, j) + 1).forEach((x) => sel.add(x));
  set({ selection: sel });
}
export function selectAllVisible() {
  set({ selection: new Set(visibleClips().map((c) => c.id)) });
}
export function stepClip(dir) {
  const ids = visibleClips().map((c) => c.id);
  if (!ids.length) return;
  const i = ids.indexOf(get().clipId);
  const n = ids[i < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, i + dir))];
  set({ _scrollTo: n });
  selectClip(n);
}
const coerce = (v) => (/^\d+$/.test(v) ? Number(v) : v);
