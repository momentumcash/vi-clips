// v2 Finder window: sources + creators in the sidebar, clips as file icons, Generate / Export in the toolbar.
import { get, set, on, persist } from "../store.js";
import { api, upload } from "../api.js";
import { esc, tc, dur, displayTitle, sourceFrame, statusTone, STATUS_LABEL, STAGE_LABEL, humanError, isBelowBar, icon } from "../util.js";
import {
  selectSource, selectClip, setInspector, generate, cancelJob, retryJob, exportClips,
  activeJob, lastJob, loadSources, notify, fail,
} from "../actions.js";
import { grouped, stepClip } from "../components/browser.js";
import { applyFilters } from "../components/filters.js";
import { hiddenIds, showActive, startReplay, canReplay, toggleClean, cuesOn, setCues } from "./show.js";
import { normStages } from "../components/sourcebar.js";
import { openImport } from "../components/importer.js";
import { openPage } from "./sheets.js";

const G = {
  film: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="2.5" y="3" width="11" height="10" rx="1.6"/><path d="M5 3v10M11 3v10M2.5 6h2.5M2.5 10h2.5M11 6h2.5M11 10h2.5"/></svg>',
  person: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="5.8" r="2.6"/><path d="M3 13.5c.8-2.6 2.7-3.8 5-3.8s4.2 1.2 5 3.8" stroke-linecap="round"/></svg>',
  plus: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><circle cx="8" cy="8" r="5.6"/><path d="M8 5.4v5.2M5.4 8h5.2"/></svg>',
  queue: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><path d="M3 4.5h10M3 8h10M3 11.5h6"/></svg>',
  chart: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><path d="M3 13V8M8 13V3.5M13 13V6.5"/></svg>',
  gear: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M3.6 12.4 5 11M11 5l1.4-1.4" stroke-linecap="round"/></svg>',
  back: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 5.5 8 10 12.5"/></svg>',
  fwd: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
  grid: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/></svg>',
  list: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><path d="M5.5 4h8M5.5 8h8M5.5 12h8"/><circle cx="2.8" cy="4" r=".6" fill="currentColor"/><circle cx="2.8" cy="8" r=".6" fill="currentColor"/><circle cx="2.8" cy="12" r=".6" fill="currentColor"/></svg>',
  share: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8M5 5.3 8 2.5l3 2.8"/><path d="M4.5 7.5H4a1 1 0 0 0-1 1V13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V8.5a1 1 0 0 0-1-1h-.5"/></svg>',
  dots: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="3.5" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="12.5" cy="8" r="1.4"/></svg>',
  search: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="7" r="4.3"/><path d="m10.3 10.3 3.2 3.2" stroke-linecap="round"/></svg>',
};

const STOP = new Set(["a", "an", "the", "of", "on", "to", "for", "and", "in", "into", "with", "his", "her", "he", "she", "at", "by", "is", "it", "its", "got"]);
/** Finder-style file name from the clip title: a few words, then .mp4 */
export function fileName(c) {
  const words = displayTitle(c).replace(/[“”"]/g, "").split(/\s+/);
  let out = [];
  for (const w of words) {
    if ((out.join(" ") + " " + w).trim().length > 26) break;
    out.push(w);
  }
  while (out.length > 2 && STOP.has(out[out.length - 1].toLowerCase().replace(/[^a-z']/g, ""))) out.pop();
  return out.join(" ").replace(/[,.;:!?—-]+$/, "") + ".mp4";
}

let anchor = null;

export function mountFinder(root) {
  root.innerHTML = `
    <aside class="f-side">
      <div class="lights" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="side-scroll"></div>
      <div class="side-foot"></div>
    </aside>
    <div class="f-bar">
      <div class="nav2"><button class="tb-btn" type="button" data-step="-1" aria-label="Previous clip">${G.back}</button><button class="tb-btn" type="button" data-step="1" aria-label="Next clip">${G.fwd}</button></div>
      <div class="f-title"></div>
      <label class="f-srcsel"><select aria-label="Source"></select>${icon.chev}</label>
      <span class="f-gap"></span>
      <div class="f-run"></div>
      <div class="viewseg" role="radiogroup" aria-label="View">
        <button class="tb-btn" type="button" data-view="grid" aria-label="Icons" title="Icons">${G.grid}</button>
        <button class="tb-btn" type="button" data-view="list" aria-label="List" title="List">${G.list}</button>
      </div>
      <button class="tb-btn" type="button" data-export aria-label="Export" title="Export selected">${G.share}</button>
      <label class="search2">${G.search}<input type="search" placeholder="Search" aria-label="Search clips" spellcheck="false"></label>
      <div class="f-more"><button class="tb-btn" type="button" data-more aria-haspopup="menu" aria-expanded="false" aria-label="More" title="More">${G.dots}</button><div class="f-menu" role="menu" hidden></div></div>
    </div>
    <div class="f-main" tabindex="-1"><div class="show" aria-live="polite"></div><div class="f-content"></div></div>
    <div class="f-status" role="status"></div>
    <div class="f-drop">Drop to import</div>`;
  const side = root.querySelector(".side-scroll");
  const foot = root.querySelector(".side-foot");
  const title = root.querySelector(".f-title");
  const run = root.querySelector(".f-run");
  const main = root.querySelector(".f-main");
  const content = root.querySelector(".f-content");
  const srcSel = root.querySelector(".f-srcsel select");
  const menu = root.querySelector(".f-menu");
  const moreBtn = root.querySelector("[data-more]");
  let seen = new Set();
  let seenSrc = null;
  const status = root.querySelector(".f-status");
  const q = root.querySelector(".search2 input");
  let importing = null; // 0..1 while uploading

  // ------------------------------------------------------------ sidebar
  function renderSide() {
    const s = get();
    const srcs = s.sources.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    side.innerHTML = `
      <div class="side-h">Sources</div>
      ${srcs
        .map(
          (x) => `<button type="button" class="side-row ${String(x.id) === String(s.sourceId) ? "on" : ""}" data-src="${esc(x.id)}" title="${esc(x.title)}">
            ${G.film}<span class="t">${esc(x.title || "Untitled")}</span>${x.status === "processing" ? `<span class="dot t-live"></span>` : ""}</button>`
        )
        .join("")}
      <button type="button" class="side-row add" data-add>${G.plus}<span class="t">Add video</span></button>
      <div class="side-h">Creators</div>
      ${s.creators.map((c) => `<button type="button" class="side-row" data-page="creators">${G.person}<span class="t">${esc(c.name)}</span></button>`).join("") || `<button type="button" class="side-row add" data-page="creators">${G.plus}<span class="t">Add creator</span></button>`}`;
    const running = s.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    foot.innerHTML = `
      <button type="button" class="side-row" data-page="queue">${G.queue}<span class="t">Queue</span>${running ? `<span class="n">${running}</span>` : ""}</button>
      <button type="button" class="side-row" data-page="analytics">${G.chart}<span class="t">Analytics</span></button>
      <button type="button" class="side-row" data-page="settings">${G.gear}<span class="t">Settings</span></button>`;
  }
  root.querySelector(".f-side").addEventListener("click", (e) => {
    const b = e.target.closest("[data-src],[data-add],[data-page]");
    if (!b) return;
    if (b.dataset.src) selectSource(num(b.dataset.src));
    else if (b.hasAttribute("data-add")) openImport();
    else openPage(b.dataset.page);
  });

  // ------------------------------------------------------------ toolbar
  function renderBar() {
    const s = get();
    const src = s.source && String(s.source.id) === String(s.sourceId) ? s.source : s.sources.find((x) => String(x.id) === String(s.sourceId));
    title.textContent = src ? `${String(src.title).split(" — ")[0]} — clips` : "ClipForge";
    const opts = s.sources.map((x) => `<option value="${esc(x.id)}" ${String(x.id) === String(s.sourceId) ? "selected" : ""}>${esc(String(x.title).split(" — ")[0])}</option>`).join("");
    if (srcSel.dataset.k !== opts) {
      srcSel.innerHTML = (s.sourceId ? "" : `<option value="">Choose a video</option>`) + opts + `<option value="__add">Add video…</option>`;
      srcSel.dataset.k = opts;
    }
    root.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === s.view)));
    root.querySelector("[data-export]").disabled = !(s.selection.size || s.clipId != null);
    if (document.activeElement !== q) q.value = s.filters.q || "";
    const job = activeJob();
    if (!s.sourceId) {
      run.innerHTML = "";
      return;
    }
    if (job) {
      const p = progressOf(job);
      run.innerHTML = `<span class="run-mini"><b>${esc(p.label)}</b>${p.pct != null ? `<span class="mono">${p.pct}%</span>` : ""}</span><button class="btn sm" type="button" data-cancel="${esc(job.id)}">Cancel</button>`;
      return;
    }
    if (!s.source || String(s.source.id) !== String(s.sourceId) || !s.clipsLoaded) {
      run.innerHTML = "";
      return;
    }
    const last = lastJob();
    const processed = s.clips.length || s.source.status === "processed" || (last && last.status === "done");
    run.innerHTML = processed
      ? `<button class="btn sm" type="button" data-gen>Generate again</button>`
      : `<button class="btn generate sm" type="button" data-gen>Generate clips</button>`;
  }
  root.querySelector(".f-bar").addEventListener("click", (e) => {
    const t = e.target;
    const st = t.closest("[data-step]");
    if (st) return stepClip(Number(st.dataset.step));
    const vw = t.closest("[data-view]");
    if (vw) {
      set({ view: vw.dataset.view });
      persist("view", vw.dataset.view);
      return;
    }
    if (t.closest("[data-export]")) {
      const s = get();
      const ids = s.selection.size ? [...s.selection] : s.clipId != null ? [s.clipId] : [];
      if (ids.length) exportClips(ids);
      return;
    }
    if (t.closest("[data-gen]")) return generate();
    const c = t.closest("[data-cancel]");
    if (c) cancelJob(num(c.dataset.cancel));
  });
  q.addEventListener("input", () => set({ filters: { ...get().filters, q: q.value.trim() } }));
  srcSel.addEventListener("change", () => {
    if (srcSel.value === "__add") {
      srcSel.value = String(get().sourceId ?? "");
      return openImport();
    }
    if (srcSel.value) selectSource(num(srcSel.value));
  });
  // More menu: Replay run (R), Clean mode (F), Sound cues
  function renderMenu() {
    const r = canReplay();
    menu.innerHTML = `
      <button type="button" role="menuitem" data-m="replay" ${r.ok ? "" : "disabled"} title="${esc(r.why || "Replays the last finished run of this video, sped up")}"><span>Replay run</span><kbd>R</kbd></button>
      <button type="button" role="menuitem" data-m="clean"><span>${document.body.classList.contains("clean") ? "Exit clean mode" : "Clean mode"}</span><kbd>F</kbd></button>
      <button type="button" role="menuitemcheckbox" aria-checked="${cuesOn()}" data-m="cues"><span>Sound cues</span><span class="chk">${cuesOn() ? "On" : "Off"}</span></button>`;
  }
  function setMenu(open) {
    menu.hidden = !open;
    moreBtn.setAttribute("aria-expanded", String(open));
    if (open) renderMenu();
  }
  moreBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    setMenu(menu.hidden);
  });
  menu.addEventListener("click", (e) => {
    const b = e.target.closest("[data-m]");
    if (!b || b.disabled) return;
    setMenu(false);
    if (b.dataset.m === "replay") startReplay();
    if (b.dataset.m === "clean") toggleClean();
    if (b.dataset.m === "cues") setCues(!cuesOn());
  });
  document.addEventListener("pointerdown", (e) => !menu.hidden && !e.target.closest(".f-more") && setMenu(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && !menu.hidden && setMenu(false));

  // ------------------------------------------------------------ main
  function render() {
    const s = get();
    content.dataset.view = s.view;
    const job = activeJob();
    if (!s.sourcesLoaded) return (content.innerHTML = skeleton());
    if (!s.sources.length) {
      content.innerHTML = `<div class="calm"><div class="drop-big"><h2>Drop a video here</h2><p>A stream, a VOD, a podcast — any length.</p><button class="btn primary" type="button" data-add style="margin-top:14px">Add video</button></div></div>`;
      return;
    }
    if (!s.sourceId) {
      content.innerHTML = `<div class="icons">${s.sources
        .map(
          (x) => `<div class="fi" data-open-src="${esc(x.id)}" tabindex="0"><span class="fi-thumb wide">${x.thumb_url ? `<img src="${esc(x.thumb_url)}" alt="" loading="lazy">` : `<span class="ph"></span>`}</span>
            <span class="fi-name">${esc(x.title || "Untitled")}</span><span class="fi-meta">${tc(x.duration_s)}</span></div>`
        )
        .join("")}</div>`;
      return;
    }
    if (!s.clipsLoaded || !s.source || String(s.source.id) !== String(s.sourceId)) return (content.innerHTML = skeleton());
    const hide = hiddenIds();
    const list = hide ? grouped(applyFilters(s.clips).filter((c) => !hide(c))) : grouped();
    const own = s.clips.filter((c) => String(c.source_id) === String(s.sourceId));
    if (!list.length) {
      if (job || showActive()) {
        content.innerHTML = ""; // the show panel carries progress, moments and the result
        return;
      }
      const last = lastJob();
      if (last && last.status === "failed" && !own.length) {
        const stg = normStages(last).find((x) => x.status === "failed");
        const where = STAGE_LABEL[stg?.name || last.current_stage] || stg?.name || "";
        const raw = last.error || stg?.error || "";
        content.innerHTML = `<div class="calm"><h2>${where ? "Stopped at " + esc(where) : "Processing stopped"}</h2><p class="err-line">${esc(humanError(raw, where))}</p>
          ${raw ? `<details class="fold det"><summary><span class="fold-t">Details</span>${icon.chev}</summary><pre class="mono raw-err">${esc(raw)}</pre></details>` : ""}
          <button class="btn primary" type="button" data-retry="${esc(last.id)}">Retry</button></div>`;
        return;
      }
      if (own.length || (last && last.status === "done")) {
        const below = own.filter(isBelowBar).length;
        content.innerHTML = (s.filters.q
          ? `<div class="calm"><h2>No clips match “${esc(s.filters.q)}”</h2></div>`
          : `<div class="calm"><h2>Nothing worth posting in this one.</h2>${below ? `<button class="btn sm" type="button" data-below>Show ${below} below-bar moment${below === 1 ? "" : "s"}</button>` : `<p>No moment scored high enough to render.</p>`}</div>`);
        return;
      }
      const src = s.source;
      content.innerHTML = `<div class="calm"><span class="fi-thumb wide" style="width:220px">${src.thumb_url ? `<img src="${esc(src.thumb_url)}" alt="">` : ""}</span><h2 style="margin-top:8px">Ready when you are</h2><p>Generate clips finds the moments worth posting in this ${esc(dur(src.duration_s))} video.</p></div>`;
      return;
    }
    const head = "";
    if (s.view === "list") {
      content.innerHTML = head + `<div class="rows2"><div class="r2 r2-head"><span></span><span>Name</span><span>Length</span><span>Score</span><span>Status</span></div>${list.map((c) => row(c, s)).join("")}</div>`;
    } else {
      content.innerHTML = head + `<div class="icons">${list.map((c) => icon2(c, s)).join("")}</div>`;
    }
    // clips that weren't on screen a moment ago drop in (same source only — switching sources doesn't animate)
    const ids = new Set(list.map((c) => c.id));
    if (seenSrc === s.sourceId && seen.size + (showActive() ? 1 : 0) > 0)
      content.querySelectorAll(".fi[data-id]").forEach((el, i) => {
        if (!seen.has(num(el.dataset.id))) {
          el.classList.add("drop");
          el.style.animationDelay = Math.min(i, 2) * 60 + "ms";
        }
      });
    seen = ids;
    seenSrc = s.sourceId;
    const on_ = content.querySelector(".on");
    if (on_ && s._scrollTo === s.clipId) on_.scrollIntoView({ block: "nearest" });
  }

  function thumb(c, s) {
    if (c.thumb_url) return `<img src="${esc(c.thumb_url)}" alt="" loading="lazy" draggable="false">`;
    const f = sourceFrame(c, s.timeline);
    return f ? `<img class="raw" src="${esc(f)}" alt="" loading="lazy" draggable="false">` : `<span class="ph">${c.status === "rendering" ? "Rendering" : ""}</span>`;
  }
  function icon2(c, s) {
    const picked = s.selection.has(c.id);
    const takes = c._takes?.length || 1;
    return `
      <div class="fi ${s.clipId === c.id ? "on" : ""} ${picked ? "picked" : ""}" data-id="${esc(c.id)}" tabindex="0" aria-selected="${s.clipId === c.id || picked}">
        <span class="fi-thumb">${thumb(c, s)}</span>
        ${takes > 1 ? `<button type="button" class="fi-takes" data-take title="The same moment from ${takes} runs — click to switch">${takes} takes</button>` : ""}
        <button type="button" class="fi-edit" data-edit>Edit</button>
        <span class="fi-name" title="${esc(displayTitle(c))}">${esc(fileName(c))}</span>
        <span class="fi-meta"><span class="dot t-${statusTone(c.status)}" title="${esc(STATUS_LABEL[c.status] || c.status)}"></span>${tc(c.duration ?? c.end_s - c.start_s)} · ${c.overall ?? "–"}</span>
      </div>`;
  }
  function row(c, s) {
    const picked = s.selection.has(c.id);
    return `<div class="r2 ${s.clipId === c.id ? "on" : ""} ${picked ? "picked" : ""}" data-id="${esc(c.id)}" tabindex="0">
      <span class="r2-thumb">${thumb(c, s)}</span><span class="t" title="${esc(displayTitle(c))}">${esc(fileName(c))}${(c._takes?.length || 1) > 1 ? ` · ${c._takes.length} takes` : ""}</span>
      <span class="m">${tc(c.duration ?? c.end_s - c.start_s)}</span><span class="m">${c.overall ?? "–"}</span><span class="m">${esc(STATUS_LABEL[c.status] || c.status)}</span></div>`;
  }
  const skeleton = () => `<div class="icons" aria-label="Loading">${'<div class="fi"><span class="fi-thumb"><span class="ph"></span></span></div>'.repeat(6)}</div>`;

  main.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-add]")) return openImport();
    if (t.closest("[data-below]")) return set({ filters: { ...get().filters, status: "below" } });
    const rt = t.closest("[data-retry]");
    if (rt) return retryJob(num(rt.dataset.retry));
    const os = t.closest("[data-open-src]");
    if (os) return selectSource(num(os.dataset.openSrc));
    const it = t.closest("[data-id]");
    if (!it) {
      if (e.target === main || t.closest(".icons, .rows2")) set({ selection: new Set() });
      return;
    }
    const id = num(it.dataset.id);
    if (t.closest("[data-take]")) {
      const g = grouped().find((x) => x.id === id);
      const ts = g?._takes || [];
      const i = ts.findIndex((x) => x.id === id || g._members?.some((m) => m.id === id && (m.variant_of ?? m.id) === x.id));
      if (ts.length > 1) selectClip(ts[(Math.max(0, i) + 1) % ts.length].id);
      return;
    }
    if (t.closest("[data-edit]")) {
      selectClip(id, { open: true });
      return;
    }
    if (e.metaKey || e.ctrlKey) {
      const sel = new Set(get().selection);
      if (!sel.size && get().clipId != null) sel.add(get().clipId);
      sel.has(id) ? sel.delete(id) : sel.add(id);
      anchor = id;
      return set({ selection: sel });
    }
    if (e.shiftKey && anchor != null) {
      const ids = grouped().map((c) => c.id);
      const a = ids.indexOf(anchor);
      const b = ids.indexOf(id);
      if (a >= 0 && b >= 0) return set({ selection: new Set(ids.slice(Math.min(a, b), Math.max(a, b) + 1)) });
    }
    anchor = id;
    if (get().selection.size) set({ selection: new Set() });
    selectClip(id);
  });
  main.addEventListener("dblclick", (e) => {
    const it = e.target.closest("[data-id]");
    if (it && !e.target.closest("[data-take]")) selectClip(num(it.dataset.id), { open: true });
  });

  // ------------------------------------------------------------ status bar
  function renderStatus() {
    const s = get();
    if (s.engine.online === false) return (status.innerHTML = `<span class="dot t-bad"></span>Engine offline`);
    if (importing != null) return (status.innerHTML = `<span class="dot t-live"></span>Importing · ${Math.round(importing * 100)}%`);
    const job = activeJob();
    if (job) {
      const p = progressOf(job);
      return (status.innerHTML = `<span class="dot t-live"></span>Generating · ${esc(p.label)}${p.pct != null ? " " + p.pct + "%" : ""}`);
    }
    if (!s.sourceId) return (status.innerHTML = `${s.sources.length} source${s.sources.length === 1 ? "" : "s"}`);
    const hide = hiddenIds();
    const list = hide ? grouped(applyFilters(s.clips).filter((c) => !hide(c))) : grouped();
    const ready = list.filter((c) => ["rendered", "approved", "exported"].includes(c.status)).length;
    const n = s.selection.size;
    status.innerHTML = n ? `${n} of ${list.length} selected` : `${list.length} clip${list.length === 1 ? "" : "s"} · ${ready} ready`;
  }

  // ------------------------------------------------------------ drag & drop import
  let depth = 0;
  root.addEventListener("dragenter", (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    depth++;
    root.classList.add("dragging");
  });
  root.addEventListener("dragover", (e) => e.preventDefault());
  root.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) root.classList.remove("dragging");
  });
  root.addEventListener("drop", async (e) => {
    e.preventDefault();
    depth = 0;
    root.classList.remove("dragging");
    const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|webm)$/i.test(x.name));
    if (!f) return notify({ tone: "bad", title: "That isn't a video file", detail: "Drop an MP4, MOV, MKV or WEBM." });
    const s = get();
    const cid = s.source?.creator_id ?? (Number(sessionStorage.getItem("cf.lastCreator")) || s.creators[0]?.id);
    if (cid == null || !s.creators.length) return openImport();
    importing = 0;
    renderStatus();
    try {
      const src = await upload(f, cid, (p) => {
        importing = p;
        renderStatus();
      });
      await loadSources();
      if (src?.id != null) await selectSource(src.id);
      notify({ tone: "ok", title: "Imported", detail: src?.title || f.name });
    } catch (er) {
      fail(er, "Upload failed");
    } finally {
      importing = null;
      renderStatus();
    }
  });

  const all = () => {
    renderSide();
    renderBar();
    render();
    renderStatus();
  };
  const offs = [
    on(["sources", "sourcesLoaded", "sourceId", "source", "creators", "jobs"], all),
    on(["clips", "clipsLoaded", "clipId", "selection", "filters", "view", "timeline"], () => {
      renderBar();
      render();
      renderStatus();
    }),
    on(["engine"], renderStatus),
    on(["showTick"], () => {
      render();
      renderStatus();
    }),
  ];
  all();
  const tick = setInterval(() => activeJob() && (renderBar(), renderStatus()), 1000);
  return () => {
    offs.forEach((f) => f());
    clearInterval(tick);
  };
}

/** One honest progress number for a job: the running stage and its % (or step count when the stage can't say). */
export function progressOf(job) {
  const st = normStages(job);
  const cur = st.find((x) => x.status === "running") || st.find((x) => x.status === "pending");
  const idx = cur ? st.indexOf(cur) : st.length;
  const label = job.status === "queued" ? "Queued" : STAGE_LABEL[cur?.name] || cur?.name || "Working";
  const pct = cur && cur.progress > 0 ? Math.round(cur.progress * 100) : null;
  // the bar follows the same number the label shows; stages that can't report % show overall position instead
  const frac = pct != null ? cur.progress : job.progress != null ? job.progress : (idx + (cur?.progress || 0)) / st.length;
  return { label, pct, frac: Math.max(0.02, Math.min(1, frac)), step: `${Math.min(idx + 1, st.length)} of ${st.length}` };
}

const num = (v) => (/^\d+$/.test(v) ? Number(v) : v);
