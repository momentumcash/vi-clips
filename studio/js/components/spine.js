// The Source Spine — the whole source as one strip: ruler, found moments as score-height brackets,
// real filmstrip frames, the measured energy curve. Fills from real job progress while processing.
import { get, on } from "../store.js";
import { esc, tc, clamp, STAGE_LABEL, sourceFrame, displayTitle, isBelowBar, momentsOf } from "../util.js";
import { selectClip, activeJob } from "../actions.js";
import { applyFilters } from "./filters.js";
import { normStages } from "./sourcebar.js";

export function mountSpine(root) {
  root.classList.add("spine");
  root.innerHTML = `
    <div class="sp-gutter">
      <span class="sp-lab">Moments</span>
      <span class="sp-lab">Frames</span>
      <span class="sp-lab">Energy</span>
    </div>
    <div class="sp-main">
      <div class="sp-ruler"></div>
      <div class="sp-lanes">
        <div class="sp-moments"></div>
        <div class="sp-frames"></div>
        <div class="sp-energy"></div>
        <div class="sp-cover" hidden><i></i></div>
        <div class="sp-head" hidden></div>
      </div>
      <div class="sp-tip" hidden></div>
    </div>
    <div class="sp-status"></div>`;
  const ruler = root.querySelector(".sp-ruler");
  const moments = root.querySelector(".sp-moments");
  const frames = root.querySelector(".sp-frames");
  const energy = root.querySelector(".sp-energy");
  const cover = root.querySelector(".sp-cover");
  const head = root.querySelector(".sp-head");
  const tip = root.querySelector(".sp-tip");
  const status = root.querySelector(".sp-status");
  const lanes = root.querySelector(".sp-lanes");

  const duration = () => {
    const s = get();
    return s.timeline?.duration || s.source?.duration_s || s.sources.find((x) => String(x.id) === String(s.sourceId))?.duration_s || 0;
  };

  function renderRuler() {
    const d = duration();
    if (!d) {
      ruler.innerHTML = "";
      return;
    }
    const w = ruler.clientWidth || 900;
    const target = d / (w / 90); // ~one label per 90px
    const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
    const step = steps.find((x) => x >= target) || 3600;
    let html = "";
    const pxPerS = w / d;
    for (let t = 0; t <= d + 0.001; t += step) {
      const p = (t / d) * 100;
      const nearEnd = (d - t) * pxPerS < 56;
      html += `<span class="tick" style="left:${p}%">${nearEnd ? "" : `<em class="mono">${tc(t)}</em>`}</span>`;
      const sub = step / 5;
      for (let k = 1; k < 5 && t + k * sub < d; k++) html += `<span class="tick minor" style="left:${((t + k * sub) / d) * 100}%"></span>`;
    }
    html += `<span class="tick end" style="left:100%"><em class="mono">${tc(d)}</em></span>`;
    ruler.innerHTML = html;
  }

  function renderMoments() {
    const s = get();
    const d = duration();
    if (!d || !s.sourceId) {
      moments.innerHTML = "";
      return;
    }
    // one bracket per moment (re-run takes + hook openings fold together — same helper as the browser)
    const all = s.clips.filter((c) => String(c.source_id) === String(s.sourceId));
    const passing = new Set(applyFilters(all).map((c) => c.id));
    const ms = momentsOf(all);
    const clips = ms.map((m) => m.all.find((c) => c.id === s.clipId) || m.best);
    const shown = new Set(ms.filter((m) => m.all.some((c) => passing.has(c.id))).map((m) => m.best.id));
    const repOf = (c) => ms.find((m) => m.all.includes(c))?.best.id;
    // heat is relative to this source, so the best moments stand out
    const live = clips.filter((c) => !isBelowBar(c) && c.status !== "rejected");
    const ov = (live.length ? live : clips).map((c) => c.overall || 0);
    const lo = Math.min(...ov);
    const hi = Math.max(...ov);
    moments.innerHTML = clips
      .slice()
      .sort((a, b) => (a.overall || 0) - (b.overall || 0)) // best drawn last (on top)
      .map((c) => {
        const l = (c.start_s / d) * 100;
        const w = Math.max(((c.end_s - c.start_s) / d) * 100, 0);
        const rel = hi > lo ? clamp(((c.overall || 0) - lo) / (hi - lo), 0, 1) : 1;
        const below = isBelowBar(c) || c.status === "rejected";
        const hgt = below ? 22 : 25 + rel * 75;
        const heat = below ? 0 : rel;
        const cls = [
          "br",
          s.clipId === c.id ? "on" : "",
          s.selection.has(c.id) ? "picked" : "",
          shown.has(repOf(c)) ? "" : "muted",
          c.status === "rejected" || c.duplicate_of ? "rej" : "",
        ].join(" ");
        return `<button type="button" class="${cls}" data-id="${esc(c.id)}" style="left:${l}%;width:${w}%;height:${hgt}%;--heat:${heat.toFixed(3)}" aria-label="${esc(displayTitle(c))} — score ${c.overall ?? "–"}, ${tc(c.start_s)}"><i></i></button>`;
      })
      .join("");
  }

  let framesKey = "";
  function renderFrames() {
    const s = get();
    const d = duration();
    const fs = s.timeline?.frames || [];
    const key = s.sourceId + "|" + fs.length + "|" + d + "|" + Math.round((frames.clientWidth || 0) / 70);
    if (key === framesKey) return;
    framesKey = key;
    root.classList.toggle("no-frames", !fs.length);
    if (!fs.length || !d) {
      frames.innerHTML = "";
      return;
    }
    // one frame per ~70 px of lane, so it reads as pictures, not a barcode
    const want = Math.max(4, Math.floor((frames.clientWidth || 1000) / 70));
    const step = Math.max(1, Math.round(fs.length / want));
    const pick = fs.filter((_, i) => i % step === 0);
    frames.innerHTML = pick
      .map((f, i) => {
        const nx = pick[i + 1]?.t ?? d;
        return `<img src="${esc(f.url)}" alt="" loading="lazy" draggable="false" style="left:${(f.t / d) * 100}%;width:${((nx - f.t) / d) * 100 + 0.05}%">`;
      })
      .join("");
  }

  let energyKey = "";
  function renderEnergy() {
    const s = get();
    const d = duration();
    const en = s.timeline?.energy || [];
    const key = s.sourceId + "|" + en.length + "|" + d;
    if (key === energyKey) return;
    energyKey = key;
    root.classList.toggle("no-energy", en.length < 2);
    if (en.length < 2 || !d) {
      energy.innerHTML = "";
      return;
    }
    // bin to ~1 point per 4px: mean power per bin → dB, then a light 3-tap smooth. Shape, not noise.
    const bins = Math.max(40, Math.min(en.length, Math.round((energy.clientWidth || 1000) / 4)));
    const acc = new Array(bins).fill(0);
    const cnt = new Array(bins).fill(0);
    for (const [t, db] of en) {
      if (!isFinite(db)) continue;
      const b = Math.min(bins - 1, Math.max(0, Math.floor((t / d) * bins)));
      acc[b] += Math.pow(10, db / 10);
      cnt[b]++;
    }
    let lv = acc.map((a, i) => (cnt[i] ? 10 * Math.log10(a / cnt[i] + 1e-12) : null));
    for (let i = 0; i < lv.length; i++) if (lv[i] == null) lv[i] = lv[i - 1] ?? -60;
    lv = lv.map((v, i) => (0.25 * (lv[i - 1] ?? v) + 0.5 * v + 0.25 * (lv[i + 1] ?? v)));
    const sorted = [...lv].sort((a, b) => a - b);
    const lo = sorted[Math.floor(sorted.length * 0.05)] ?? -60;
    const hi = sorted[sorted.length - 1] ?? 0;
    const span = Math.max(1, hi - lo);
    const pts = lv.map((db, i) => `${(((i + 0.5) / bins) * 1000).toFixed(2)},${(40 - clamp((db - lo) / span, 0, 1) * 36).toFixed(2)}`);
    energy.innerHTML = `<svg viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true"><path class="e-fill" d="M0,40 L${pts.join(" L")} L1000,40 Z"/><path class="e-line" d="M${pts.join(" L")}"/></svg>`;
  }

  function renderCover() {
    const s = get();
    const job = activeJob();
    if (!job) {
      cover.hidden = true;
      status.innerHTML = statusIdle();
      return;
    }
    const st = normStages(job);
    const tr = st.find((x) => x.name === "transcribe");
    const cur = st.find((x) => x.status === "running") || st.find((x) => x.status === "pending");
    let cov = s.timeline?.words_coverage;
    if (cov == null && tr) cov = tr.status === "done" || tr.status === "skipped" ? 1 : tr.status === "running" ? tr.progress || 0 : 0;
    cover.hidden = cov == null || cov >= 1;
    cover.style.setProperty("--cov", clamp(cov || 0, 0, 1));
    status.innerHTML = `<span class="dot t-live"></span><span>${job.status === "queued" ? "Queued" : esc(STAGE_LABEL[cur?.name] || cur?.name || "Working")}</span>`;
  }
  function statusIdle() {
    const s = get();
    if (!s.sourceId) return "";
    const clips = momentsOf(s.clips.filter((c) => String(c.source_id) === String(s.sourceId))).map((m) => m.best);
    const live = clips.filter((c) => c.status !== "rejected" && !c.duplicate_of);
    const src = s.source || s.sources.find((x) => String(x.id) === String(s.sourceId));
    const head = s.filters.scope === "all" && src ? `<span class="ss-src" title="${esc(src.title)}">${esc(src.title)}</span>` : "";
    if (!clips.length) return `${head}<span class="mono">${tc(duration())}</span>`;
    return `${head}<span class="ss-n"><b>${live.length}</b>${live.length === 1 ? "moment" : "moments"}</span>${
      clips.length > live.length ? `<span>${clips.length - live.length} under the bar</span>` : ""
    }<span class="mono">${tc(duration())} source</span>`;
  }

  function renderHead() {
    const s = get();
    const d = duration();
    const c = s.clips.find((x) => x.id === s.clipId);
    if (s.playhead == null || !d || !c || String(c.source_id) !== String(s.sourceId)) {
      head.hidden = true;
      return;
    }
    head.hidden = false;
    head.style.left = (s.playhead / d) * 100 + "%";
  }

  // hover preview
  moments.addEventListener("pointerover", (e) => {
    const b = e.target.closest(".br");
    if (!b) return;
    const c = get().clips.find((x) => String(x.id) === b.dataset.id);
    if (!c) return;
    tip.innerHTML = `
      ${c.thumb_url ? `<img src="${esc(c.thumb_url)}" alt="">` : sourceFrame(c, get().timeline) ? `<img class="raw" src="${esc(sourceFrame(c, get().timeline))}" alt="">` : `<span class="tip-nothumb"></span>`}
      <span class="tip-txt"><b>${esc(displayTitle(c))}</b><small class="mono">${tc(c.start_s)} · ${Math.round(c.end_s - c.start_s)}s · score ${c.overall ?? "–"}${c.status === "rejected" ? " · under the bar" : ""}</small></span>`;
    tip.hidden = false;
    const main = root.querySelector(".sp-main").getBoundingClientRect();
    const r = b.getBoundingClientRect();
    const tw = tip.offsetWidth;
    tip.style.left = clamp(r.left + r.width / 2 - main.left - tw / 2, 0, main.width - tw) + "px";
  });
  moments.addEventListener("pointerout", (e) => {
    if (!e.relatedTarget || !e.relatedTarget.closest?.(".br")) tip.hidden = true;
  });
  moments.addEventListener("click", (e) => {
    const b = e.target.closest(".br");
    if (b) selectClip(/^\d+$/.test(b.dataset.id) ? Number(b.dataset.id) : b.dataset.id);
  });

  const all = () => {
    renderRuler();
    renderMoments();
    renderFrames();
    renderEnergy();
    renderCover();
    renderHead();
    root.classList.toggle("is-empty", !get().sourceId);
  };
  const ro = new ResizeObserver(() => {
    renderRuler();
    energyKey = "";
    renderEnergy();
    renderFrames();
  });
  ro.observe(ruler);
  const o1 = on(["timeline", "source", "sourceId", "sources"], all);
  const o2 = on(["clips", "clipId", "selection", "filters"], () => {
    renderMoments();
    renderCover();
  });
  const o3 = on(["jobs"], renderCover);
  const o4 = on(["playhead"], renderHead);
  all();
  return () => {
    o1();
    o2();
    o3();
    o4();
    ro.disconnect();
  };
}
