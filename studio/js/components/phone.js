// The phone: plays the real rendered MP4 (video_url) of the selected clip. Muted by default, never autoplays.
import { get, set, on } from "../store.js";
import { esc, tcf, icon, clamp, sourceFrame, rejectText, presetLabel, STAGE_LABEL } from "../util.js";
import { rerender, setInspector, activeJob } from "../actions.js";

export const phoneCtl = { toggle() {}, seek() {}, jkl() {} };

export function mountPhone(root) {
  root.classList.add("phone");
  root.innerHTML = `
    <div class="device">
      <div class="screen">
        <video playsinline muted preload="metadata" disablepictureinpicture></video>
        <div class="safe" hidden aria-hidden="true">
          <div class="sz top"><span>UI · 12%</span></div>
          <div class="sz cap"><span>Captions 55–72%</span></div>
          <div class="sz bottom"><span>UI · 20%</span></div>
          <div class="sz actions"><i></i><i></i><i></i><i></i></div>
        </div>
        <div class="screen-state"></div>
        <div class="screen-flag" hidden></div>
      </div>
    </div>
    <div class="transport">
      <button class="icon-btn play" type="button" aria-label="Play">${icon.play}</button>
      <div class="scrub" role="slider" aria-label="Seek" tabindex="0" aria-valuemin="0">
        <div class="scrub-track"><i class="scrub-fill"></i><i class="scrub-buf"></i></div>
        <i class="scrub-knob"></i>
      </div>
      <span class="time mono"><b class="cur">0:00.0</b><span>/</span><span class="tot">0:00.0</span></span>
      <button class="icon-btn snd" type="button" aria-label="Sound off — click to turn on" title="Sound is off">${icon.mute}</button>
    </div>
    <div class="phone-tools">
      <label class="toggle"><input type="checkbox" class="sz-toggle"><span>Safe zones</span></label>
      <span class="ph-preset" title="Caption style of this render — change it in the inspector"></span>
      <button class="link edit" type="button">Trim</button>
    </div>`;

  const v = root.querySelector("video");
  const playB = root.querySelector(".play");
  const sndB = root.querySelector(".snd");
  const scrub = root.querySelector(".scrub");
  const fill = root.querySelector(".scrub-fill");
  const buf = root.querySelector(".scrub-buf");
  const knob = root.querySelector(".scrub-knob");
  const cur = root.querySelector(".cur");
  const tot = root.querySelector(".tot");
  const state = root.querySelector(".screen-state");
  const flag = root.querySelector(".screen-flag");
  const safe = root.querySelector(".safe");
  const szT = root.querySelector(".sz-toggle");
  const preset = root.querySelector(".ph-preset");
  let mode = "clip"; // "clip" | "source"
  let srcKey = "";
  let raf = 0;
  let rate = 1;

  try { szT.checked = localStorage.getItem("clipforge.ui.safe") === "1"; } catch {}
  safe.hidden = !szT.checked;
  szT.addEventListener("change", () => {
    safe.hidden = !szT.checked;
    try { localStorage.setItem("clipforge.ui.safe", szT.checked ? "1" : "0"); } catch {}
  });

  const clipNow = () => {
    const s = get();
    const base = s.clips.find((c) => c.id === s.clipId);
    return s.clip && s.clip.id === s.clipId ? { ...s.clip, ...(base || {}) } : base || null;
  };

  function srcNow() {
    const s = get();
    return s.source && String(s.source.id) === String(s.sourceId) ? s.source : s.sources.find((x) => String(x.id) === String(s.sourceId)) || null;
  }

  function load() {
    const c = clipNow();
    let url = c?.video_url && c.status !== "candidate" ? c.video_url : "";
    let poster = c?.thumb_url || (url ? "" : sourceFrame(c, get().timeline));
    const src = !c ? srcNow() : null;
    mode = !c && src?.video_url ? "source" : "clip";
    if (mode === "source") {
      url = src.video_url;
      // a real frame from the source strip at its own aspect (the 16:9 thumb pillarboxes vertical video)
      const fs = get().timeline?.frames || [];
      poster = fs.length ? fs[Math.floor(fs.length * 0.1)].url : src.thumb_url || "";
    }
    root.classList.toggle("is-source", mode === "source");
    root.classList.toggle("src-vertical", mode === "source" && (src?.height || 0) > (src?.width || 1));
    const key = url ? url + "|" + (c?.file_key || "") + "|" + (c?.updated_at || "") : "poster|" + poster;
    if (key !== srcKey) {
      srcKey = key;
      v.pause();
      rate = 1;
      v.playbackRate = 1;
      if (url) {
        v.poster = poster;
        v.src = mode === "source" ? url : url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(c.updated_at || c.file_key || "");
        v.load();
      } else {
        v.removeAttribute("src");
        v.poster = poster;
        v.load();
      }
      paintTime();
    }
    preset.textContent = c ? presetLabel(c.caption_preset) : "";
    root.querySelector(".edit").disabled = !c;
    renderState(c, !!url);
  }

  function renderState(c, hasVideo) {
    flag.hidden = true;
    state.innerHTML = "";
    root.classList.toggle("no-video", !hasVideo);
    if (!c) {
      if (mode === "source") {
        const job = activeJob();
        const st = job && (job.stages || []).find((x) => x.status === "running");
        flag.hidden = false;
        flag.innerHTML = job
          ? `<span class="dot t-live"></span>Source · ${esc(job.status === "queued" ? "Queued" : STAGE_LABEL[st?.name] || st?.name || "Working")}`
          : `Source`;
      } else state.innerHTML = get().clips.length || !get().clipsLoaded || !get().source ? "" : `<p>No clips yet</p>`;
      return;
    }
    if (c.status === "rendering") {
      if (hasVideo) {
        flag.hidden = false;
        flag.innerHTML = `<span class="dot t-live"></span>Rendering new version`;
      } else state.innerHTML = `<p><span class="dot t-live"></span>Rendering</p>`;
      return;
    }
    if (c.status === "failed") {
      const bad = (c.qc?.checks || []).filter((x) => !x.pass);
      state.innerHTML = `
        <div class="ss-fail">
          <b>Failed QC</b>
          ${bad.length ? `<ul>${bad.slice(0, 4).map((x) => `<li><span>${esc(x.name)}</span>${x.detail ? `<small>${esc(x.detail)}</small>` : ""}</li>`).join("")}</ul>` : `<small>${esc(c.error || "Render didn't pass")}</small>`}
          <button class="btn sm" type="button" data-rr>Retry render</button>
        </div>`;
      return;
    }
    if (!hasVideo) {
      const below = c.status === "rejected" || c.status === "candidate";
      state.innerHTML = `
        <div class="ss-none">
          <p>${below ? "Not rendered" : "No render yet"}</p>
          ${c.status === "rejected" && c.reject_reason ? `<small>${esc(rejectText(c.reject_reason))}</small>` : ""}
          <button class="btn sm" type="button" data-rr>Render</button>
        </div>`;
    }
  }
  state.addEventListener("click", (e) => {
    if (e.target.closest("[data-rr]")) {
      const c = clipNow();
      if (c) rerender(c.id);
    }
  });

  // ---- transport
  const toggle = () => {
    if (!v.src) return;
    v.paused ? v.play().catch(() => {}) : v.pause();
  };
  playB.addEventListener("click", toggle);
  v.addEventListener("click", toggle);
  sndB.addEventListener("click", () => {
    v.muted = !v.muted;
    sndB.innerHTML = v.muted ? icon.mute : icon.sound;
    sndB.title = v.muted ? "Sound is off" : "Sound is on";
    sndB.setAttribute("aria-label", v.muted ? "Sound off — click to turn on" : "Sound on — click to mute");
  });
  v.addEventListener("play", () => {
    playB.innerHTML = icon.pause;
    playB.setAttribute("aria-label", "Pause");
    tick();
  });
  v.addEventListener("pause", () => {
    playB.innerHTML = icon.play;
    playB.setAttribute("aria-label", "Play");
    cancelAnimationFrame(raf);
    paintTime();
  });
  v.addEventListener("loadedmetadata", paintTime);
  v.addEventListener("progress", paintBuf);
  v.addEventListener("seeked", paintTime);
  v.addEventListener("ended", () => set({ playhead: null }));

  function tick() {
    paintTime();
    if (!v.paused) raf = requestAnimationFrame(tick);
  }
  function paintTime() {
    const d = v.duration && isFinite(v.duration) ? v.duration : clipNow()?.duration || 0;
    const t = v.currentTime || 0;
    const p = d ? clamp(t / d, 0, 1) : 0;
    fill.style.transform = `scaleX(${p})`;
    knob.style.left = p * 100 + "%";
    cur.textContent = tcf(t);
    tot.textContent = tcf(d);
    scrub.setAttribute("aria-valuemax", String(d.toFixed(1)));
    scrub.setAttribute("aria-valuenow", String(t.toFixed(1)));
    const c = clipNow();
    if (v.src && (t > 0 || !v.paused)) {
      if (mode === "source") set({ playhead: t });
      else if (c) set({ playhead: (c.start_s || 0) + t });
    }
  }
  function paintBuf() {
    try {
      const d = v.duration;
      if (!d || !v.buffered.length) return;
      buf.style.transform = `scaleX(${clamp(v.buffered.end(v.buffered.length - 1) / d, 0, 1)})`;
    } catch {}
  }
  let dragging = false;
  const seekAt = (x) => {
    const r = scrub.getBoundingClientRect();
    const d = v.duration;
    if (!d || !isFinite(d)) return;
    v.currentTime = clamp((x - r.left) / r.width, 0, 1) * d;
    paintTime();
  };
  scrub.addEventListener("pointerdown", (e) => {
    dragging = true;
    scrub.setPointerCapture(e.pointerId);
    seekAt(e.clientX);
  });
  scrub.addEventListener("pointermove", (e) => dragging && seekAt(e.clientX));
  scrub.addEventListener("pointerup", () => (dragging = false));
  scrub.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      e.stopPropagation();
      v.currentTime = clamp(v.currentTime + (e.key === "ArrowLeft" ? -1 : 1), 0, v.duration || 0);
    }
  });

  root.querySelector(".edit").addEventListener("click", () => {
    setInspector(true);
    requestAnimationFrame(() => document.querySelector(".insp [data-sec=trim]")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  });

  phoneCtl.toggle = toggle;
  phoneCtl.jkl = (k) => {
    if (!v.src) return;
    if (k === "k") {
      v.pause();
      rate = 1;
      v.playbackRate = 1;
    } else if (k === "j") {
      v.pause();
      v.currentTime = Math.max(0, v.currentTime - 2);
    } else if (k === "l") {
      if (v.paused) {
        rate = 1;
        v.playbackRate = 1;
        v.play().catch(() => {});
      } else {
        rate = rate >= 2 ? 1 : rate + 0.5;
        v.playbackRate = rate;
      }
    }
  };
  phoneCtl.seek = (t) => {
    if (v.src) v.currentTime = t;
  };

  const off = on(["clip", "clipId", "clips", "clipsLoaded", "timeline", "source", "sourceId", "jobs"], load);
  load();
  return () => {
    off();
    v.pause();
    cancelAnimationFrame(raf);
    set({ playhead: null });
  };
}
