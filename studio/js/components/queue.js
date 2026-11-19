// Job stage timeline — used in the Studio while processing and on the Queue page.
import { esc, dur, STAGE_LABEL, ago } from "../util.js";
import { normStages } from "./sourcebar.js";

const ts = (v) => (v == null ? null : typeof v === "number" ? (v < 1e12 ? v * 1000 : v) : Date.parse(v));

export function stageSeconds(x) {
  const a = ts(x.started_at);
  if (!a) return null;
  const b = ts(x.ended_at) || (x.status === "running" ? Date.now() : null);
  return b ? Math.max(0, (b - a) / 1000) : null;
}

export function stageList(job, { compact } = {}) {
  const st = normStages(job);
  return `
    <ol class="stages ${compact ? "compact" : ""}">
      ${st
        .map((x) => {
          const sec = stageSeconds(x);
          const p = x.status === "running" ? x.progress || 0 : x.status === "done" || x.status === "skipped" ? 1 : 0;
          return `
          <li class="sg k-${esc(x.status)}">
            <span class="sg-dot"></span>
            <span class="sg-name">${esc(STAGE_LABEL[x.name] || x.name)}</span>
            <span class="sg-bar"><i style="transform:scaleX(${p})"></i></span>
            <span class="sg-meta mono">${
              x.status === "running" ? (x.progress > 0 ? Math.round(x.progress * 100) + "%" : sec != null ? dur(sec) : "…") : x.status === "skipped" ? "kept" : sec != null ? dur(sec) : ""
            }</span>
            ${x.detail && !compact && !(x.status === "running" && /%/.test(x.detail)) ? `<span class="sg-detail">${esc(x.detail)}</span>` : ""}
            ${x.error ? `<span class="sg-err">${esc(x.error)}${x.attempts > 1 ? ` · attempt ${x.attempts}` : ""}</span>` : ""}
          </li>`;
        })
        .join("")}
    </ol>`;
}

export function jobSummary(job) {
  const st = normStages(job);
  const total = st.reduce((a, x) => a + (stageSeconds(x) || 0), 0);
  return { total, created: ago(job.created_at) };
}
