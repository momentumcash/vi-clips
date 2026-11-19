// Settings — defaults the engine uses. Integrations show exactly what's missing; nothing is hidden.
import { get, on } from "../store.js";
import { api } from "../api.js";
import { esc, icon, TARGETS, PRESETS } from "../util.js";
import { fail, notify, loadIntegrations } from "../actions.js";

const WHISPER = ["tiny", "base", "small", "medium", "large-v3-turbo", "distil-large-v3", "large-v3"];

// each field: [label, paths (nested "a.b" or flat), kind, options]
const GROUPS = [
  [
    "Brain",
    [
      ["Language model", ["llm.provider", "llm_provider", "llm"], "select", [["claude_cli", "Claude (this Mac's CLI)"], ["anthropic_api", "Anthropic API key"]]],
      ["Model", ["llm.model", "llm_model"], "text", "Provider default"],
      ["Transcription", ["whisper_model", "whisper.model", "transcribe.whisper_model"], "whisper"],
    ],
  ],
  [
    "Clips",
    [
      ["Most clips per video", ["max_clips"], "num"],
      ["Default caption preset", ["default_caption_preset"], "select", PRESETS],
      ["Progress bar on clips", ["render.progress_bar"], "bool"],
      ["Subtle punch-in on the payoff", ["render.punch_in"], "bool"],
    ],
  ],
  [
    "Automation",
    [
      ["Process new videos automatically", ["automation.auto_process", "auto_process"], "bool"],
      ["Scan every", ["automation.scan_interval_min", "scan_interval_min"], "num", "min"],
      ["Clips per day, max", ["automation.daily_limit", "daily_limit"], "num"],
      ["Keep clips scoring at least", ["automation.min_score", "min_score"], "num"],
      ["Default length", ["automation.default_target", "default_target"], "select", TARGETS],
    ],
  ],
  ["Storage", [["Where media lives", ["storage_status.local_root", "storage.backend"], "readonly"]]],
];

function read(obj, paths) {
  for (const p of paths) {
    const v = p.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), obj);
    if (v !== undefined && (typeof v !== "object" || v === null)) return { path: p, value: v };
  }
  return null;
}
function patchFor(path, value) {
  const parts = path.split(".");
  const out = {};
  let o = out;
  parts.forEach((k, i) => (i === parts.length - 1 ? (o[k] = value) : (o = o[k] = {})));
  return out;
}

const INT_NAME = {
  local_files: "Local files",
  youtube_api: "YouTube",
  twitch_api: "Twitch",
  ytdlp_authorized: "Download from a link",
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
  instagram: "Instagram Reels",
  claude_cli: "Claude CLI",
  anthropic_api: "Anthropic API",
  s3_storage: "S3 storage",
};
const KIND = [
  ["llm", "AI"],
  ["discovery", "Find videos"],
  ["publish", "Publish"],
  ["storage", "Storage"],
];
function kindOf(i) {
  if (KIND.some(([k]) => k === i.kind)) return i.kind;
  if (/claude|anthropic|llm|openai/.test(i.name)) return "llm";
  if (/s3|storage/.test(i.name)) return "storage";
  return i.kind || "discovery";
}
// What each one needs, in plain words (the env var names sit behind "What it needs").
const NEED = {
  youtube_api: "a YouTube API key",
  twitch_api: "a Twitch app ID and secret",
  tiktok: "an audited TikTok developer app and a login token",
  youtube: "a Google sign-in for uploading (an API key alone can't upload)",
  instagram: "a business Instagram account and a token",
  anthropic_api: "an Anthropic API key (optional — the Claude CLI works without it)",
  s3_storage: "an S3 bucket and keys (optional — media stays on this Mac)",
};
function docParts(i) {
  const d = i.doc || "";
  const m = d.match(/^(https?:\/\/\S+)\s*(?:—\s*)?(.*)$/);
  return { url: m ? m[1] : "", text: m ? m[2] : d };
}

export function mount(el) {
  el.innerHTML = `
    <section class="pg">
      <header class="pg-head"><h1>Settings</h1></header>
      <div class="pg-body set"></div>
    </section>`;
  const body = el.querySelector(".set");
  let settings = null;
  let err = null;

  async function load() {
    try {
      settings = await api.settings();
      err = null;
    } catch (e) {
      err = e;
    }
    loadIntegrations();
    render();
  }

  function field([label, paths, kind0, opts0]) {
    let kind = kind0;
    let opts = opts0;
    const r = settings ? read(settings, paths) : null;
    if (!r) return "";
    const v = r.value;
    let ctl;
    if (kind === "whisper") {
      const cached = settings.whisper_models_cached || WHISPER;
      kind = "select";
      opts = cached.map((w) => [w, w]);
    }
    if (kind === "bool") ctl = `<label class="toggle"><input type="checkbox" data-path="${r.path}" data-kind="bool" ${v ? "checked" : ""}><span>${v ? "On" : "Off"}</span></label>`;
    else if (kind === "select") {
      const o = opts.some(([k]) => String(k) === String(v)) ? opts : [[v, v], ...opts];
      ctl = `<div class="select"><select data-path="${r.path}">${o.map(([k, l]) => `<option value="${esc(k)}" ${String(k) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${icon.chev}</div>`;
    } else if (kind === "num") ctl = `<span class="num-in"><input type="number" class="mono" data-path="${r.path}" data-kind="num" value="${esc(v)}">${opts ? `<small>${esc(opts)}</small>` : ""}</span>`;
    else if (kind === "readonly") ctl = `<span class="mono muted">${esc(v)}</span>`;
    else ctl = `<input type="text" class="mono" data-path="${r.path}" value="${esc(v ?? "")}" placeholder="${esc(opts || "")}">`;
    return `<div class="set-row"><span>${esc(label)}</span>${ctl}</div>`;
  }

  function render() {
    if (err) {
      body.innerHTML = `<div class="pg-empty"><p>${esc(err.message)}</p>${err.detail ? `<p class="muted">${esc(err.detail)}</p>` : ""}</div>`;
      return;
    }
    const ints = get().integrations || [];
    const groups = GROUPS.map(([name, fields]) => {
      const rows = fields.map(field).join("");
      return rows ? `<section class="set-g"><h2>${esc(name)}</h2>${rows}</section>` : "";
    }).join("");
    const intRow = (i) => {
      const n = (i.needs || []).length;
      const { url, text } = docParts(i);
      const plain = i.status === "ready" ? (i.name === "local_files" || i.name === "claude_cli" ? "" : text) : NEED[i.name] ? "Needs " + NEED[i.name] + "." : "";
      return `
      <div class="int-row">
        <span class="dot t-${i.status === "ready" ? "ok" : "warn"}"></span>
        <span class="int-n"><b>${esc(INT_NAME[i.name] || i.name.replace(/_/g, " "))}</b></span>
        <span class="int-s">
          <span>${i.status === "ready" ? "Ready" : `Blocked · needs ${n || ""} ${n === 1 ? "key" : "keys"}`}</span>
          ${plain ? `<small>${esc(plain.charAt(0).toUpperCase() + plain.slice(1))}</small>` : ""}
          ${n || url ? `<details class="fold det"><summary><span class="fold-t">More</span>${icon.chev}</summary>
            ${n ? `<p class="int-vars mono">${(i.needs || []).map(esc).join("<br>")}</p>` : ""}
            ${text && i.status !== "ready" ? `<p class="int-doc">${esc(text)}</p>` : ""}
            ${url ? `<a class="int-link" href="${esc(url)}" target="_blank" rel="noopener">Setup guide</a>` : ""}
          </details>` : ""}
        </span>
      </div>`;
    };
    const groupsInt = KIND.map(([k, label]) => {
      const rows = ints.filter((i) => kindOf(i) === k);
      return rows.length ? `<h3 class="int-g">${label}</h3>${rows.map(intRow).join("")}` : "";
    }).join("");
    body.innerHTML = `
      ${groups || (settings ? `<section class="set-g"><h2>Engine settings</h2><pre class="mono raw-json">${esc(JSON.stringify(settings, null, 2))}</pre></section>` : "")}
      <section class="set-g">
        <h2>Integrations</h2>
        ${ints.length ? groupsInt : `<p class="muted">The engine reported no integrations.</p>`}
        <p class="muted int-foot">Keys go in the engine's environment (see README), then restart ClipForge.</p>
      </section>`;
  }

  body.addEventListener("change", async (e) => {
    const t = e.target;
    const path = t.dataset.path;
    if (!path) return;
    const value = t.dataset.kind === "bool" ? t.checked : t.dataset.kind === "num" ? Number(t.value) : t.value;
    try {
      const r = await api.patchSettings(patchFor(path, value));
      settings = r && typeof r === "object" && !Array.isArray(r) && Object.keys(r).length ? r : settings;
      notify({ tone: "ok", title: "Saved", ms: 1600 });
      if (!r) await load();
      else render();
    } catch (er) {
      fail(er, "Couldn't save setting");
      load();
    }
  });

  const off = on(["integrations"], render);
  load();
  return off;
}
