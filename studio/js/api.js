// ClipForge API client — fetch + SSE against the engine at the same origin (127.0.0.1, port VI_ENGINE_PORT). Real API only.

export class ApiError extends Error {
  constructor(status, body = {}) {
    super(body.error || `Request failed (${status})`);
    this.status = status;
    this.detail = body.detail || "";
    this.fix = body.fix || "";
    this.body = body;
    this.offline = status === 0;
  }
}

const OFFLINE = {
  error: "Engine offline",
  detail: "Nothing answered at the engine address.",
  fix: "Start it with ./start.sh",
};

async function req(method, path, body, extraHeaders) {
  const headers = { ...(extraHeaders || {}) };
  let payload;
  if (body !== undefined) {
    if (body instanceof Blob) payload = body;
    else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, OFFLINE);
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { error: text.slice(0, 200) }; }
  }
  if (!res.ok) throw new ApiError(res.status, data || {});
  return data;
}

const qs = (o) => {
  const p = Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? "?" + new URLSearchParams(p).toString() : "";
};

export const api = {
  ping: () => req("GET", "/api/ping"),
  overview: () => req("GET", "/api/overview"),

  creators: () => req("GET", "/api/creators"),
  createCreator: (b) => req("POST", "/api/creators", b),
  updateCreator: (id, b) => req("PATCH", `/api/creators/${id}`, b),
  deleteCreator: (id) => req("DELETE", `/api/creators/${id}`),
  scanCreator: (id) => req("POST", `/api/creators/${id}/scan`, {}),

  sources: (creator_id) => req("GET", "/api/sources" + qs({ creator_id })),
  source: (id) => req("GET", `/api/sources/${id}`),
  timeline: (id) => req("GET", `/api/sources/${id}/timeline`),
  importPath: (b) => req("POST", "/api/sources/import", b),
  importUrl: (b) => req("POST", "/api/sources/url", b),

  jobs: (status) => req("GET", "/api/jobs" + qs({ status })),
  job: (id) => req("GET", `/api/jobs/${id}`),
  jobLive: (id) => req("GET", `/api/jobs/${id}/live`),
  events: (since) => req("GET", "/api/events" + qs({ since })),
  startJob: (source_id, settings) => req("POST", "/api/jobs", { source_id, settings }),
  retryJob: (id) => req("POST", `/api/jobs/${id}/retry`, {}),
  cancelJob: (id) => req("POST", `/api/jobs/${id}/cancel`, {}),

  clips: (q) => req("GET", "/api/clips" + qs(q)),
  clip: (id) => req("GET", `/api/clips/${id}`),
  patchClip: (id, b) => req("PATCH", `/api/clips/${id}`, b),
  renderClip: (id) => req("POST", `/api/clips/${id}/render`, {}),
  variants: (id) => req("POST", `/api/clips/${id}/variants`, {}),
  rescore: (id) => req("POST", `/api/clips/${id}/rescore`, {}),
  export: (clip_ids, dir) => req("POST", "/api/export", dir ? { clip_ids, dir } : { clip_ids }),
  addMetrics: (id, b) => req("POST", `/api/clips/${id}/metrics`, b),

  analytics: () => req("GET", "/api/analytics"),
  settings: () => req("GET", "/api/settings"),
  patchSettings: (b) => req("PATCH", "/api/settings", b),
  integrations: () => req("GET", "/api/integrations"),
  publish: (clipId, b) => req("POST", `/api/publish/${clipId}`, b || {}),
};

/** Raw-body upload with real byte progress (XHR — fetch has no upload progress). */
export function upload(file, creatorId, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/sources/upload");
    x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    if (creatorId != null) x.setRequestHeader("X-Creator-Id", String(creatorId));
    x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    x.onerror = () => reject(new ApiError(0, OFFLINE));
    x.onload = () => {
      let d = {};
      try { d = JSON.parse(x.responseText || "{}"); } catch {}
      x.status < 400 ? resolve(d) : reject(new ApiError(x.status, d));
    };
    x.send(file);
  });
}

/** Server-sent events. Calls onMessage(obj) for every message; onState(bool) on connect / drop. */
export function stream(onMessage, onState) {
  let es;
  let closed = false;
  let retry;
  const open = () => {
    es = new EventSource("/api/stream");
    es.onopen = () => onState?.(true);
    const handle = (ev) => {
      let d = null;
      try { d = JSON.parse(ev.data); } catch { return; }
      if (d && typeof d === "object") {
        if (!d.type && ev.type && ev.type !== "message") d.type = ev.type;
        onMessage(d);
      }
    };
    es.onmessage = handle;
    for (const t of ["job", "clip", "source", "event", "progress", "stage", "clip_ready", "job_update", "export"]) {
      es.addEventListener(t, handle);
    }
    es.onerror = () => {
      onState?.(false);
      es.close();
      if (!closed) retry = setTimeout(open, 3000);
    };
  };
  open();
  return () => {
    closed = true;
    clearTimeout(retry);
    es?.close();
  };
}
