// ClipForge v2 — one light screen: wallpaper, phone, Finder window. (The previous studio lives at /v1/.)
import { get, set, on } from "./store.js";
import { esc, copyText, icon } from "./util.js";
import { checkEngine, setStatus, exportClips, notify, setInspector, selectSource } from "./actions.js";
import { selectAllVisible, stepClip } from "./components/browser.js";
import { mountPhone2, phone2 } from "./v2/phone.js";
import { mountFinder } from "./v2/finder.js";
import { initSheets, openPage, closePage, pageOpen } from "./v2/sheets.js";
import { mountShow, startReplay, stopReplay, replaying, toggleClean } from "./v2/show.js";
import { fit } from "./v2/fit.js";

const banner = document.getElementById("banner");
const notice = document.getElementById("notice");

// the edit sheet opens on double-click / Enter / Edit, never by itself
set({ inspector: false, filters: { ...get().filters, scope: "source", q: "" } });

mountPhone2(document.getElementById("stage"));
const finderEl = document.getElementById("finder");
mountFinder(finderEl);
initSheets(finderEl);
const winTag = document.createElement("span");
winTag.className = "win-tag";
winTag.hidden = true;
finderEl.appendChild(winTag);
mountShow({ panel: finderEl.querySelector(".show"), chip: document.getElementById("chip"), ticker: document.getElementById("ticker"), winTag });
fit();
addEventListener("resize", fit);

// ------------------------------------------------------------ default source: the newest one
let picked = false;
on(["sourcesLoaded", "sources"], (s) => {
  if (picked || !s.sourcesLoaded) return;
  picked = true;
  if (!s.sourceId && s.sources.length) {
    const newest = s.sources.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
    selectSource(newest.id);
  }
});

// ------------------------------------------------------------ hash → sheets (links like #/creators keep working)
function route() {
  const name = location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  if (["queue", "creators", "analytics", "settings", "library"].includes(name)) openPage(name);
  else if (pageOpen()) closePage(true);
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------ banner + notices
function renderBanner() {
  const s = get();
  if (s.engine.online === false) {
    banner.hidden = false;
    banner.innerHTML = `
      <span class="dot t-bad"></span>
      <b>Engine offline</b>
      <span>Start it with <span class="mono">./start.sh</span> in the vi-clips folder — this page reconnects by itself.</span>
      <button class="btn sm" type="button" data-copy="./start.sh">${icon.copy}Copy command</button>
      <button class="btn ghost sm" type="button" data-recheck>Check again</button>`;
  } else banner.hidden = true;
}
banner.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]");
  if (c) copyText(c.dataset.copy).then(() => notify({ tone: "ok", title: "Copied", detail: c.dataset.copy }));
  if (e.target.closest("[data-recheck]")) checkEngine();
});

function renderNotice() {
  const n = get().notice;
  if (!n) {
    notice.hidden = true;
    notice.innerHTML = "";
    return;
  }
  notice.hidden = false;
  notice.className = "notice tone-" + (n.tone || "info");
  notice.innerHTML = `
    <span class="dot t-${n.tone === "bad" ? "bad" : n.tone === "live" ? "live" : n.tone === "ok" ? "ok" : "idle"}"></span>
    <div class="n-body">
      <b>${esc(n.title)}</b>
      ${n.detail ? `<p>${esc(n.detail)}</p>` : ""}
      ${n.fix ? `<p class="n-fix">${esc(n.fix)}</p>` : ""}
      ${n.path ? `<div class="n-path"><code class="mono">${esc(n.path)}</code><button class="btn sm" type="button" data-copy-path>${icon.copy}Copy path</button></div>` : ""}
    </div>
    ${n.action ? `<a class="btn sm" href="${esc(n.action.href)}">${esc(n.action.label)}</a>` : ""}
    <button class="icon-btn" type="button" aria-label="Dismiss" data-dismiss>${icon.x}</button>`;
}
notice.addEventListener("click", (e) => {
  if (e.target.closest("[data-dismiss]")) set({ notice: null });
  if (e.target.closest("[data-copy-path]")) copyText(get().notice?.path || "").then(() => notify({ tone: "ok", title: "Path copied" }));
  if (e.target.closest("a")) set({ notice: null });
});

// ------------------------------------------------------------ keyboard
function cols() {
  const g = document.querySelector(".f-main .icons");
  if (!g) return 1;
  return getComputedStyle(g).gridTemplateColumns.split(" ").length || 1;
}
document.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t.closest?.("input, textarea, select, [contenteditable=true]")) {
    if (e.key === "Escape") t.blur();
    return;
  }
  const s = get();
  const k = e.key.toLowerCase();
  if (e.key === "Escape") {
    if (replaying()) return stopReplay(true);
    if (pageOpen()) return closePage();
    if (s.inspector) return setInspector(false);
    if (s.selection.size) return set({ selection: new Set() });
    if (s.notice) return set({ notice: null });
    return;
  }
  if (pageOpen() || s.inspector) return; // a sheet is in front: leave keys to it
  if ((e.metaKey || e.ctrlKey) && k === "a") {
    e.preventDefault();
    return selectAllVisible();
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (k === "r") return startReplay();
  if (k === "f") return toggleClean();
  if (replaying()) return; // the replay owns the screen until it ends (Esc stops it)
  if (e.key === " ") {
    if (t.closest?.("button, a")) return;
    e.preventDefault();
    phone2.toggle();
  } else if (k === "j" || k === "k" || k === "l") phone2.jkl(k);
  else if (e.key === "ArrowRight") (e.preventDefault(), stepClip(1));
  else if (e.key === "ArrowLeft") (e.preventDefault(), stepClip(-1));
  else if (e.key === "ArrowDown") (e.preventDefault(), stepClip(s.view === "list" ? 1 : cols()));
  else if (e.key === "ArrowUp") (e.preventDefault(), stepClip(s.view === "list" ? -1 : -cols()));
  else if (e.key === "Enter" && s.clipId != null) setInspector(true);
  else if (k === "a" || k === "x") {
    const ids = s.selection.size ? [...s.selection] : s.clipId != null ? [s.clipId] : [];
    if (ids.length) setStatus(ids, k === "a" ? "approved" : "rejected");
  } else if (k === "e") {
    const ids = s.selection.size ? [...s.selection] : s.clipId != null ? [s.clipId] : [];
    if (ids.length) exportClips(ids);
  }
});

// ------------------------------------------------------------ boot
on(["engine"], renderBanner);
on(["notice"], renderNotice);
renderBanner();
checkEngine();
route();
