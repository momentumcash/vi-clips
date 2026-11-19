// Studio — the one screen: long video in → the clips worth posting out.
import { get, set, on } from "../store.js";
import { esc, tc, dur, ago, presetLabel, TARGETS, RIGHTS_LABEL, rightsTone, STATUS_LABEL, statusTone, STAGE_LABEL, humanError, icon, prettyEvent, displayTitle, momentsOf } from "../util.js";
import { activeJob, lastJob, selectSource, selectClip, retryJob, loadOverview } from "../actions.js";
import { mountSourcebar, srcLabel, srcTone, normStages } from "../components/sourcebar.js";
import { mountPhone } from "../components/phone.js";
import { mountFilters } from "../components/filters.js";
import { mountBrowser } from "../components/browser.js";
import { mountInspector } from "../components/inspector.js";
import { mountSpine } from "../components/spine.js";
import { stageList } from "../components/queue.js";
import { renderDropbox, openImport } from "../components/importer.js";

export function mount(el) {
  el.innerHTML = `
    <section class="studio">
      <header class="sb"></header>
      <div class="bay">
        <div class="col-phone"></div>
        <div class="col-browse">
          <div class="fl"></div>
          <div class="browse-body"><div class="br-host"></div><div class="state" hidden></div></div>
          <div class="selbar" hidden></div>
        </div>
        <aside class="ins" aria-label="Inspector"></aside>
      </div>
      <footer class="sp"></footer>
      <div class="first" hidden></div>
      <div class="overview" hidden></div>
    </section>`;
  const studio = el.querySelector(".studio");
  const state = el.querySelector(".state");
  const first = el.querySelector(".first");
  const ov = el.querySelector(".overview");
  const offs = [
    mountSourcebar(el.querySelector(".sb")),
    mountPhone(el.querySelector(".col-phone")),
    mountFilters(el.querySelector(".fl")),
    mountBrowser(el.querySelector(".br-host"), el.querySelector(".selbar")),
    mountInspector(el.querySelector(".ins")),
    mountSpine(el.querySelector(".sp")),
  ];
  let dropOff = null;
  loadOverview();

  function mode() {
    const s = get();
    if (s.engine.online === false && !s.sources.length) return "offline";
    if (s.engine.online == null || !s.sourcesLoaded) return "boot";
    if (!s.sources.length && !s.creators.length) return "first";
    if (!s.sources.length) return "first";
    if (!s.sourceId) return "overview";
    const own = s.clips.filter((c) => String(c.source_id) === String(s.sourceId));
    if (s.filters.scope === "all" ? s.clips.length : own.length) return "clips";
    if (!s.clipsLoaded || !s.source || String(s.source.id) !== String(s.sourceId)) return "loading";
    if (activeJob()) return "processing";
    const lj = lastJob();
    if (lj?.status === "failed") return "failed";
    if (lj?.status === "done" || s.source?.status === "processed") return "none-found";
    if (s.source?.status === "blocked") return "blocked";
    return "fresh";
  }

  function render() {
    const m = mode();
    studio.dataset.mode = m;
    el.querySelector(".fl").hidden = !(m === "clips" || (m === "none-found" && get().clips.length));
    first.hidden = m !== "first";
    ov.hidden = m !== "overview";
    if (m === "first") {
      if (!dropOff) {
        first.innerHTML = `<div class="first-box"><div class="fb-drop"></div><a class="btn ghost" href="#/creators?new=1">Add a creator</a></div>`;
        dropOff = renderDropbox(first.querySelector(".fb-drop"));
      }
    } else if (dropOff) {
      dropOff();
      dropOff = null;
      first.innerHTML = "";
    }
    if (m === "overview") renderOverview();
    const showState = !["clips", "first", "overview"].includes(m);
    state.hidden = !showState;
    if (showState) state.innerHTML = stateHTML(m);
  }

  function stateHTML(m) {
    const s = get();
    const src = s.source || s.sources.find((x) => String(x.id) === String(s.sourceId));
    if (m === "boot" || m === "loading") return `<div class="st-skel" aria-label="Loading">${"<i></i>".repeat(6)}</div>`;
    if (m === "offline") return `<div class="st-fresh"><p class="st-big">Waiting for the engine</p></div>`;
    if (m === "processing") {
      const job = activeJob();
      return `<div class="st-proc"><div class="st-h"><b>Finding moments</b><small class="mono">${esc(TARGETS.find((t) => t[0] === (job.settings?.target || s.target))?.[1] || "Auto")} · ${esc(presetLabel(job.settings?.caption_preset || s.preset))}</small></div>${stageList(job)}</div>`;
    }
    if (m === "failed") {
      const job = lastJob();
      const st = normStages(job).find((x) => x.status === "failed");
      const where = STAGE_LABEL[st?.name || job.current_stage] || st?.name || job.current_stage || "";
      const raw = job.error || st?.error || "";
      return `
        <div class="st-proc">
          <div class="st-h"><b>${where ? "Stopped at " + esc(where) : "Processing stopped"}</b></div>
          <p class="st-err">${esc(humanError(raw, where))}</p>
          ${raw ? `<details class="fold det"><summary><span class="fold-t">Details</span>${icon.chev}</summary><pre class="mono raw-err">${esc(raw)}</pre></details>` : ""}
          ${stageList(job, { compact: true })}
          <button class="btn primary" type="button" data-retry="${esc(job.id)}">${st ? "Retry from " + esc(STAGE_LABEL[st.name] || st.name) : "Retry"}</button>
        </div>`;
    }
    if (m === "none-found") {
      return `
        <div class="st-fresh">
          <p class="st-big">Nothing worth posting in this one.</p>
          <p class="muted">No moment scored high enough to render.</p>
        </div>`;
    }
    if (m === "blocked") {
      return `<div class="st-fresh"><p class="st-big">This source is blocked.</p><p class="muted">Rights are ${esc(RIGHTS_LABEL[src?.rights_status] || src?.rights_status || "unknown")}. <a href="#/creators">Set creator rights</a></p></div>`;
    }
    // fresh
    const r = src?.rights_status || "unknown";
    return `
      <div class="st-fresh">
        <div class="sf-frame">${src?.thumb_url ? `<img src="${esc(src.thumb_url)}" alt="">` : ""}</div>
        <p class="st-big">${esc(src?.title || "Untitled")}</p>
        <p class="sf-meta mono">${tc(src?.duration_s)}${src?.width ? ` · ${src.width}×${src.height}` : ""}${src?.fps ? ` · ${Math.round(src.fps)} fps` : ""}</p>
        <p class="sf-meta"><span class="dot t-${rightsTone(r)}"></span>${esc(RIGHTS_LABEL[r] || r)}<i>·</i>Not processed</p>
      </div>`;
  }

  function renderOverview() {
    const s = get();
    const o = s.overview || {};
    const q = o.queue || {};
    // one card per moment: hook variants fold into their parent
    const top = momentsOf(o.top_all || o.top_clips || []).map((m) => m.best);
    const act = o.activity || [];
    const recent = s.sources.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 6);
    ov.innerHTML = `
      <div class="ov-line">
        <span><b class="mono">${o.clips_today ?? "–"}</b> clips today</span>
        <span><b class="mono">${q.running ?? 0}</b> running</span>
        <span><b class="mono">${q.queued ?? 0}</b> queued</span>
        <span><b class="mono">${o.creators_monitored ?? 0}</b> creators monitored</span>
      </div>
      <section class="ov-top">
        <header><h2>Best clips</h2></header>
        ${top.length ? `<div class="ov-strip">${top
          .slice(0, 8)
          .map(
            (c) => `<button type="button" class="ov-clip" data-clip="${esc(c.id)}" data-src="${esc(c.source_id)}">
              <span class="cthumb">${c.thumb_url ? `<img src="${esc(c.thumb_url)}" alt="" loading="lazy">` : ""}<span class="cdur mono">${tc(c.duration ?? c.end_s - c.start_s)}</span></span>
              <span class="ctitle">${esc(displayTitle(c))}</span>
              <span class="crow"><span class="cscore mono">${c.overall ?? "–"}</span><span class="cstat"><span class="dot t-${statusTone(c.status)}"></span>${esc(STATUS_LABEL[c.status] || c.status)}</span></span>
            </button>`
          )
          .join("")}</div>` : `<p class="muted">Nothing rendered yet.</p>`}
      </section>
      <div class="ov-cols">
        <section class="ov-src">
          <header><h2>Sources</h2><button class="btn ghost sm" type="button" data-import>Import video</button></header>
          <ul>${recent
            .map(
              (x) => `<li><button type="button" data-src="${esc(x.id)}">
                <span class="src-thumb">${x.thumb_url ? `<img src="${esc(x.thumb_url)}" alt="" loading="lazy">` : ""}</span>
                <span class="src-txt"><b>${esc(x.title || "Untitled")}</b><small>${esc(x.creator_name || "")}</small></span>
                <span class="mono">${tc(x.duration_s)}</span>
                <span class="src-state"><span class="dot t-${srcTone(x.status)}"></span>${esc(srcLabel(x.status))}</span>
              </button></li>`
            )
            .join("")}</ul>
        </section>
        <section class="ov-act">
          <header><h2>Activity</h2></header>
          ${act.length ? `<ol class="log">${act
            .slice(0, 6)
            .map((e) => `<li class="lv-${esc(e.level || "info")}"><span class="mono">${esc(ago(e.ts))}</span><span>${esc(prettyEvent(e.message || e.kind))}</span></li>`)
            .join("")}</ol>` : `<p class="muted">No activity yet.</p>`}
        </section>
      </div>`;
  }

  el.addEventListener("click", async (e) => {
    const t = e.target;
    const rt = t.closest("[data-retry]");
    if (rt) retryJob(/^\d+$/.test(rt.dataset.retry) ? Number(rt.dataset.retry) : rt.dataset.retry);
    if (t.closest("[data-import]")) openImport();
    const oc = t.closest(".ov-clip");
    if (oc) {
      await selectSource(num(oc.dataset.src));
      selectClip(num(oc.dataset.clip));
      return;
    }
    const sb = t.closest(".ov-src [data-src]");
    if (sb) selectSource(num(sb.dataset.src));
  });

  offs.push(on(["sources", "sourcesLoaded", "creators", "sourceId", "source", "clips", "clipsLoaded", "jobs", "engine", "overview", "filters", "settings"], render));
  render();
  const tick = setInterval(() => studio.dataset.mode === "processing" && render(), 1000);
  return () => {
    clearInterval(tick);
    offs.forEach((f) => f && f());
    dropOff?.();
  };
}
const num = (v) => (/^\d+$/.test(v) ? Number(v) : v);
