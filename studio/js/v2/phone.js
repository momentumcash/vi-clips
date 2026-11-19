// v2 phone: a real-looking iPhone that plays the selected clip (or the source while nothing is cut yet).
// The feed overlay is decorative and generic (no logos, handles or counts) and can be hidden.
import { get, set, on } from "../store.js";
import { esc, displayTitle, sourceFrame, STAGE_LABEL } from "../util.js";
import { activeJob, rerender } from "../actions.js";
import { cuesOn, setCues, onCues } from "./sound.js";

export const phone2 = { toggle() {}, jkl() {} };

const I = {
  heart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20Z"/></svg>',
  comment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4.5 12a7.5 7 0 1 1 3.3 5.8L4.5 19l1-3.3A6.7 6.7 0 0 1 4.5 12Z"/></svg>',
  mark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M7 4.5h10v15l-5-3.6-5 3.6Z"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M13.5 5.5 20 11.5l-6.5 6v-3.6C9 13.9 6 15.6 4 19c.6-5.3 3.6-9.4 9.5-10Z"/></svg>',
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4.5 10.5 12 4.5l7.5 6V19a.5.5 0 0 1-.5.5h-4.5v-5h-5v5H5a.5.5 0 0 1-.5-.5Z"/></svg>',
  find: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="6"/><path d="m15.5 15.5 4 4" stroke-linecap="round"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 6v12M6 12h12"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4.5 13.5 7 6h10l2.5 7.5V18a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1Z"/><path d="M4.5 13.5h4l1 2h5l1-2h4"/></svg>',
  me: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8.5" r="3.5"/><path d="M5 19.5c1.2-3.4 3.8-5 7-5s5.8 1.6 7 5" stroke-linecap="round"/></svg>',
  sig: '<svg viewBox="0 0 18 11" fill="currentColor"><rect x="0" y="7" width="3" height="4" rx=".8"/><rect x="5" y="5" width="3" height="6" rx=".8"/><rect x="10" y="2.5" width="3" height="8.5" rx=".8"/><rect x="15" y="0" width="3" height="11" rx=".8"/></svg>',
  bat: '<svg viewBox="0 0 26 12" fill="none"><rect x=".5" y=".5" width="22" height="11" rx="3.2" stroke="currentColor" opacity=".45"/><rect x="2.2" y="2.2" width="17" height="7.6" rx="1.8" fill="currentColor"/><rect x="23.6" y="4" width="1.8" height="4" rx=".8" fill="currentColor" opacity=".45"/></svg>',
  play: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M4.5 2.8v10.4L13 8z"/></svg>',
  pause: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5h2.6v11H4zM9.4 2.5H12v11H9.4z"/></svg>',
  mute: '<svg viewBox="0 0 16 16"><path d="M2.5 6h2.4L8.5 3v10L4.9 10H2.5z" fill="currentColor"/><path d="M11 6l3.5 4M14.5 6L11 10" stroke="currentColor" stroke-width="1.3"/></svg>',
  sound: '<svg viewBox="0 0 16 16"><path d="M2.5 6h2.4L8.5 3v10L4.9 10H2.5z" fill="currentColor"/><path d="M10.8 5.6a3.4 3.4 0 010 4.8M12.6 3.8a6 6 0 010 8.4" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  cues: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M4.5 11V7.5a3.5 3.5 0 0 1 7 0V11l1 1.5h-9Z"/><path d="M6.8 13.6a1.3 1.3 0 0 0 2.4 0" stroke-linecap="round"/></svg>',
  cuesOff: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M4.5 11V7.5a3.5 3.5 0 0 1 7 0V11l1 1.5h-9Z"/><path d="M6.8 13.6a1.3 1.3 0 0 0 2.4 0M2.5 2.5l11 11" stroke-linecap="round"/></svg>',
  layers: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 2.5 14 5.5 8 8.5 2 5.5Z"/><path d="M2 8.5l6 3 6-3"/></svg>',
};

const mmss = (t) => {
  t = Math.max(0, t || 0);
  return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
};

export function mountPhone2(root) {
  root.innerHTML = `
    <div class="iphone-shadow" aria-hidden="true"></div>
    <div class="iphone">
      <span class="glint l" aria-hidden="true"></span><span class="glint r" aria-hidden="true"></span>
      <span class="iphone-btns"></span>
      <div class="screen2">
        <video playsinline muted preload="auto" disablepictureinpicture></video>
        <div class="scan" hidden><i></i></div>
        <div class="island"><span class="cam"></span></div>
        <div class="feed" aria-hidden="true">
          <div class="feed-status"><span>9:41</span><span class="st-r">${I.sig}${I.bat}</span></div>
          <div class="feed-top"><span>Following</span><b>For You</b></div>
          <div class="feed-rail">${I.heart}${I.comment}${I.mark}${I.share}</div>
          <div class="feed-cap"><b></b><span></span></div>
          <div class="feed-tabs">${I.home}${I.find}<span class="plus">${I.plus}</span>${I.inbox}${I.me}</div>
        </div>
        <div class="screen-tag" hidden></div>
        <div class="screen-msg"></div>
      </div>
    </div>
    <div class="stage-ctl">
      <button class="tc-pill" type="button" aria-label="Play">${I.play}<span>0:00</span></button>
      <button class="round-btn snd" type="button" aria-label="Sound off — turn on" title="Sound is off">${I.mute}</button>
      <button class="round-btn cue" type="button" aria-pressed="false" aria-label="Sound cues" title="Sound cues">${I.cuesOff}</button>
      <button class="round-btn ovl" type="button" aria-pressed="true" aria-label="Show the feed overlay" title="Feed overlay">${I.layers}</button>
    </div>`;
  const phoneEl = root.querySelector(".iphone");
  const v = root.querySelector("video");
  const pill = root.querySelector(".tc-pill");
  const pillT = pill.querySelector("span");
  const snd = root.querySelector(".snd");
  const ovl = root.querySelector(".ovl");
  const tag = root.querySelector(".screen-tag");
  const msg = root.querySelector(".screen-msg");
  const scanEl = root.querySelector(".scan");
  const cueB = root.querySelector(".cue");
  let tagOverride = null;
  const paintCue = (v) => {
    cueB.innerHTML = v ? I.cues : I.cuesOff;
    cueB.setAttribute("aria-pressed", String(v));
    cueB.title = v ? "Sound cues on" : "Sound cues off";
  };
  paintCue(cuesOn());
  onCues(paintCue);
  cueB.addEventListener("click", () => setCues(!cuesOn()));
  const capB = root.querySelector(".feed-cap b");
  const capS = root.querySelector(".feed-cap span");
  let key = "";
  let mode = "clip";
  let raf = 0;

  let feedOn = true;
  try { feedOn = localStorage.getItem("clipforge.v2.feed") !== "0"; } catch {}
  const paintFeed = () => {
    phoneEl.classList.toggle("no-feed", !feedOn);
    ovl.setAttribute("aria-pressed", String(feedOn));
    ovl.setAttribute("aria-label", feedOn ? "Hide the feed overlay" : "Show the feed overlay");
  };
  paintFeed();
  ovl.addEventListener("click", () => {
    feedOn = !feedOn;
    try { localStorage.setItem("clipforge.v2.feed", feedOn ? "1" : "0"); } catch {}
    paintFeed();
  });

  const clipNow = () => {
    const s = get();
    const base = s.clips.find((c) => c.id === s.clipId);
    return s.clip && s.clip.id === s.clipId ? { ...s.clip, ...(base || {}) } : base || null;
  };
  const srcNow = () => {
    const s = get();
    return s.source && String(s.source.id) === String(s.sourceId) ? s.source : null;
  };

  function load() {
    const s = get();
    const c = clipNow();
    let url = c?.video_url && c.status !== "candidate" ? c.video_url : "";
    let poster = c?.thumb_url || (c && !url ? sourceFrame(c, s.timeline) : "");
    const src = !c ? srcNow() : null;
    mode = !c && src?.video_url ? "source" : "clip";
    if (mode === "source") {
      url = src.video_url;
      const fs = s.timeline?.frames || [];
      poster = fs.length ? fs[Math.floor(fs.length * 0.1)].url : src.thumb_url || "";
    }
    phoneEl.classList.toggle("src-v", mode === "source" && (src?.height || 0) > (src?.width || 1));
    phoneEl.classList.toggle("is-src", mode === "source");
    const k = url ? url + "|" + (c?.updated_at || "") : "p|" + poster;
    if (k !== key) {
      key = k;
      v.pause();
      v.poster = poster || "";
      if (url) v.src = mode === "source" ? url : url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(c.updated_at || c.file_key || "");
      else v.removeAttribute("src");
      v.load();
      paint();
    }
    capB.textContent = c ? displayTitle(c) : src ? src.title || "" : "";
    capS.textContent = c ? c.description || "" : "";
    // tag + message
    tag.hidden = true;
    msg.innerHTML = "";
    if (tagOverride) {
      tag.hidden = false;
      tag.innerHTML = `<i class="pulse"></i>${esc(tagOverride)}`;
    } else if (mode === "source") {
      const job = activeJob();
      const st = job && (job.stages || []).find((x) => x.status === "running");
      tag.hidden = false;
      tag.innerHTML = job ? `<i class="pulse"></i>Source · ${esc(job.status === "queued" ? "Queued" : STAGE_LABEL[st?.name] || st?.name || "Working")}` : "Source";
    } else if (c && c.status === "rendering") {
      tag.hidden = false;
      tag.textContent = url ? "Rendering a new version" : "Rendering";
    } else if (c && !url) {
      msg.innerHTML = `<div><p>Not rendered</p><button class="btn sm" type="button" data-rr style="margin-top:10px">Render</button></div>`;
    }
  }
  msg.addEventListener("click", (e) => {
    if (e.target.closest("[data-rr]")) {
      const c = clipNow();
      if (c) rerender(c.id);
    }
  });

  function paint() {
    pillT.textContent = mmss(v.currentTime);
    pill.firstElementChild.outerHTML = v.paused ? I.play : I.pause;
    pill.setAttribute("aria-label", v.paused ? "Play" : "Pause");
    const c = clipNow();
    if (v.src && (v.currentTime > 0 || !v.paused)) set({ playhead: mode === "source" ? v.currentTime : (c?.start_s || 0) + v.currentTime });
  }
  const tick = () => {
    paint();
    if (!v.paused) raf = requestAnimationFrame(tick);
  };
  const toggle = () => {
    if (!v.src) return;
    v.paused ? v.play().catch(() => {}) : v.pause();
  };
  v.addEventListener("play", tick);
  v.addEventListener("pause", paint);
  v.addEventListener("seeked", paint);
  v.addEventListener("loadedmetadata", paint);
  v.addEventListener("click", toggle);
  pill.addEventListener("click", toggle);
  snd.addEventListener("click", () => {
    v.muted = !v.muted;
    snd.innerHTML = v.muted ? I.mute : I.sound;
    snd.title = v.muted ? "Sound is off" : "Sound is on";
    snd.setAttribute("aria-label", v.muted ? "Sound off — turn on" : "Sound on — mute");
  });

  phone2.toggle = toggle;
  phone2.jkl = (k) => {
    if (!v.src) return;
    if (k === "k") v.pause();
    else if (k === "j") v.currentTime = Math.max(0, v.currentTime - 2);
    else if (k === "l") v.paused ? v.play().catch(() => {}) : (v.playbackRate = v.playbackRate >= 2 ? 1 : v.playbackRate + 0.5);
  };
  phone2.video = v;
  /** scan line in sync with real transcription coverage (0–1), or null to hide */
  phone2.scan = (f) => {
    if (f == null) return (scanEl.hidden = true);
    scanEl.hidden = false;
    scanEl.style.setProperty("--p", Math.max(0, Math.min(1, f)).toFixed(4));
  };
  phone2.setTag = (t) => {
    if (t === tagOverride) return;
    tagOverride = t;
    load();
  };

  const off = on(["clip", "clipId", "clips", "clipsLoaded", "timeline", "source", "sourceId", "jobs"], load);
  load();
  return () => {
    off();
    cancelAnimationFrame(raf);
  };
}
