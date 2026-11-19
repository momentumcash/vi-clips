// v2 sheets: light panels that drop from the Finder window's title bar.
// - the edit sheet hosts the shared inspector (trimmed by CSS to trim / captions / style / Re-render / Approve, score folded last)
// - page sheets host the existing page modules (Queue, Creators, Analytics, Settings, Library)
import { get, on } from "../store.js";
import { setInspector } from "../actions.js";
import { mountInspector } from "../components/inspector.js";

const PAGES = {
  queue: () => import("../pages/queue.js"),
  creators: () => import("../pages/creators.js"),
  analytics: () => import("../pages/analytics.js"),
  settings: () => import("../pages/settings.js"),
  library: () => import("../pages/library.js"),
};

let host = null;
let page = null; // { name, el, back, unmount }
let paintEdit = () => {};

export function initSheets(finderEl) {
  host = finderEl;
  // edit sheet
  const back = document.createElement("div");
  back.className = "sheet2-back";
  back.hidden = true;
  const sh = document.createElement("div");
  sh.className = "sheet2 edit";
  sh.setAttribute("role", "dialog");
  sh.setAttribute("aria-label", "Edit clip");
  sh.hidden = true;
  const insp = document.createElement("aside");
  sh.appendChild(insp);
  host.append(back, sh);
  mountInspector(insp);
  back.addEventListener("click", () => setInspector(false));
  const paint = () => {
    const s = get();
    const open = !!s.inspector && s.clipId != null && !page;
    sh.hidden = !open;
    back.hidden = !open;
  };
  on(["inspector", "clipId"], paint);
  paintEdit = paint;
  paint();
}

export function pageOpen() {
  return page?.name || null;
}

export async function openPage(name) {
  if (!PAGES[name] || !host) return;
  if (page?.name === name) return;
  closePage(true);
  setInspector(false);
  const back = document.createElement("div");
  back.className = "sheet2-back";
  const el = document.createElement("div");
  el.className = "sheet2 wide";
  el.setAttribute("role", "dialog");
  el.innerHTML = `<div class="sheet2-body"></div><div class="sheet2-foot"><button class="btn primary sm" type="button" data-done>Done</button></div>`;
  host.append(back, el);
  page = { name, el, back, unmount: null };
  back.addEventListener("click", () => closePage());
  el.querySelector("[data-done]").addEventListener("click", () => closePage());
  const mod = await PAGES[name]();
  if (page?.name !== name) return;
  page.unmount = mod.mount(el.querySelector(".sheet2-body")) || null;
}

export function closePage(silent) {
  if (!page) return;
  page.unmount?.();
  page.el.remove();
  page.back.remove();
  page = null;
  paintEdit();
  if (!silent && location.hash) history.replaceState(null, "", location.pathname + location.search);
}
