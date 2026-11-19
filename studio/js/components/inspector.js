// Inspector: score breakdown, hook/payoff, transcript, trim nudges, captions, crop, preset, concerns, QC, actions.
import { get, set, on } from "../store.js";
import { esc, tc, tcf, dur, clamp, icon, SCORE_KEYS, STATUS_LABEL, statusTone, PRESETS, RIGHTS_OK, RIGHTS_LABEL, rightsTone, TARGETS, displayTitle, rootOf, wordsBetween, momentsOf } from "../util.js";
import { setInspector, rerender, setStatus, exportClips, makeVariants, fail, notify, sourceJobs, selectClip, loadClipDetail } from "../actions.js";
import { api } from "../api.js";


export function mountInspector(root) {
  root.classList.add("insp");
  let id = null;
  let draft = null; // staged edits
  let pending = false;
  let moreOpen = false;

  const clipNow = () => {
    const s = get();
    const base = s.clips.find((c) => c.id === s.clipId);
    return s.clip && s.clip.id === s.clipId ? { ...s.clip, ...(base || {}) } : base || null;
  };

  function freshDraft(c) {
    return {
      start_s: c.start_s,
      end_s: c.end_s,
      title: displayTitle(c),
      caption_preset: c.caption_preset || get().preset,
      captions: (c.captions || []).map((x) => ({ ...x })),
      crop: { mode: c.crop?.mode || "auto", x: c.crop?.x ?? 0.5 },
    };
  }
  function dirtyKeys(c) {
    if (!draft || !c) return [];
    const d = [];
    if (Math.abs(draft.start_s - c.start_s) > 1e-3) d.push("start_s");
    if (Math.abs(draft.end_s - c.end_s) > 1e-3) d.push("end_s");
    if (draft.title !== displayTitle(c)) d.push("title");
    if (draft.caption_preset !== (c.caption_preset || get().preset)) d.push("caption_preset");
    if (JSON.stringify(draft.captions.map((x) => x.text)) !== JSON.stringify((c.captions || []).map((x) => x.text))) d.push("captions");
    if (draft.crop.mode !== (c.crop?.mode || "auto") || (draft.crop.mode === "manual" && Math.abs(draft.crop.x - (c.crop?.x ?? 0.5)) > 1e-3)) d.push("crop");
    return d;
  }
  function edits(c) {
    const out = {};
    for (const k of dirtyKeys(c)) {
      if (k === "crop") out.crop = { ...(c.crop || {}), mode: draft.crop.mode, x: draft.crop.x };
      else if (k === "start_s" || k === "end_s") out[k] = Math.round(draft[k] * 1000) / 1000;
      else out[k] = draft[k];
    }
    return out;
  }

  function render() {
    const s = get();
    root.hidden = !s.inspector;
    document.querySelector(".studio")?.classList.toggle("insp-open", !!s.inspector && !!s.clipId);
    if (!s.inspector) return;
    const c = clipNow();
    if (!c) {
      root.innerHTML = `<div class="insp-empty"><p>Select a clip to inspect it.</p></div>`;
      id = null;
      return;
    }
    if (c.id !== id) {
      id = c.id;
      draft = freshDraft(c);
      moreOpen = false;
    }
    const dk = dirtyKeys(c);
    const dirty = dk.length > 0;
    const src = s.sources.find((x) => x.id === c.source_id) || (s.source?.id === c.source_id ? s.source : null);
    const rights = c.rights_status || src?.rights_status || "unknown";
    const canExport = RIGHTS_OK.includes(rights);
    const len = draft.end_s - draft.start_s;
    const prevBody = root.querySelector(".insp-body");
    const sameClip = root.dataset.cid === String(c.id);
    const keepScroll = prevBody && sameClip ? prevBody.scrollTop : 0;
    const folds = Object.fromEntries([...root.querySelectorAll("details.fold")].map((d) => [d.dataset.fold, d.open]));
    root.dataset.cid = String(c.id);

    root.innerHTML = `
      <header class="insp-head">
        <div class="ih-top">
          <span class="cstat"><span class="dot t-${statusTone(c.status)}"></span>${esc(STATUS_LABEL[c.status] || c.status)}</span>
          <span class="mono ih-range">${tc(draft.start_s)}–${tc(draft.end_s)}</span>
          ${openings(c)}
          <button class="icon-btn" type="button" data-close aria-label="Close inspector">${icon.x}</button>
        </div>
        <input class="ih-title" data-k="title" value="${esc(draft.title)}" aria-label="Clip title" spellcheck="false">
      </header>
      <div class="insp-body">
        ${hookSec(c)}
        ${trimSec(c, len)}
        ${captionSec(c)}
        ${cropSec(c, src)}
        ${presetSec()}
        ${scoreSec(c)}
        ${transcriptSec(c)}
        ${concernsSec(c, rights, canExport)}
        ${qcSec(c)}
      </div>
      <footer class="insp-foot">
        ${dirty ? `<span class="if-dirty"><span class="dot t-live"></span>${dk.length} unsaved</span><button class="btn ghost sm" type="button" data-act="discard">Discard</button>` : `<button class="btn ghost sm" type="button" data-act="more" aria-expanded="${moreOpen}">More</button>`}
        <span class="if-gap"></span>
        ${actions(c, dirty, canExport)}
      </footer>
      ${moreOpen && !dirty ? moreMenu(c, canExport, rights) : ""}`;
    if (sameClip) root.querySelectorAll("details.fold").forEach((d) => d.dataset.fold in folds && (d.open = folds[d.dataset.fold]));
    if (keepScroll) root.querySelector(".insp-body").scrollTop = keepScroll;
    drawCropWindow();
    root.querySelectorAll("textarea[data-cap]").forEach(grow);
  }
  function grow(t) {
    t.style.height = "auto";
    t.style.height = t.scrollHeight + 2 + "px";
  }

  function actions(c, dirty, canExport) {
    if (dirty) return `<button class="btn primary" type="button" data-act="save-render" ${pending ? "disabled" : ""}>Save & re-render</button>`;
    if (c.status === "failed") return `<button class="btn primary" type="button" data-act="render">Retry render</button>`;
    if (c.status === "candidate" || (c.status === "rejected" && !c.video_url))
      return `<button class="btn" type="button" data-act="render">Render</button>${c.status === "candidate" ? `<button class="btn primary" type="button" data-act="approve">Approve</button>` : ""}`;
    if (c.status === "approved")
      return `<button class="btn" type="button" data-act="render">Re-render</button><button class="btn primary" type="button" data-act="export" ${canExport ? "" : 'aria-disabled="true"'}>Export</button>`;
    if (c.status === "exported") return `<button class="btn" type="button" data-act="render">Re-render</button><button class="btn" type="button" data-act="export">Export again</button>`;
    return `<button class="btn" type="button" data-act="render" ${c.status === "rendering" ? "disabled" : ""}>Re-render</button><button class="btn primary" type="button" data-act="approve" ${c.status === "rendering" ? "disabled" : ""}>Approve</button>`;
  }

  function moreMenu(c, canExport, rights) {
    const pubs = (get().integrations || []).filter((i) => i.kind === "publish");
    const ready = pubs.filter((i) => i.status === "ready");
    return `
      <div class="more-menu" role="menu">
        ${c.status !== "rejected" ? `<button type="button" role="menuitem" data-act="reject">Reject</button>` : `<button type="button" role="menuitem" data-act="unreject">Move back to candidates</button>`}
        <button type="button" role="menuitem" data-act="variants">Make 3 hook variants</button>
        <button type="button" role="menuitem" data-act="export" ${canExport ? "" : 'aria-disabled="true"'}>Export now</button>
        <button type="button" role="menuitem" data-act="publish" ${ready.length ? "" : "disabled"}>Publish</button>
        ${ready.length ? "" : `<p class="mm-why">Publishing is off — no platform is connected. <a href="#/settings">See what each needs</a></p>`}
      </div>`;
  }

  function openings(c) {
    return takesSeg(c) + openingsSeg(c);
  }
  function takesSeg(c) {
    const m = momentsOf(get().clips.filter((x) => String(x.source_id) === String(c.source_id))).find((mm) => mm.all.some((x) => x.id === c.id));
    if (!m || m.takes.length < 2) return "";
    const cur = m.takes.find((t) => t.openings.some((o) => o.id === c.id));
    return `<span class="ih-tl">Take</span><div class="seg sm ih-open ih-takes" role="radiogroup" aria-label="Take" title="Same moment found in ${m.takes.length} runs">${m.takes
      .map((t, i) => `<button type="button" role="radio" data-open="${esc(t.root.id)}" aria-checked="${t === cur}" title="Take ${i + 1} · run of ${esc(String(t.root.created_at || "").slice(0, 16).replace("T", " "))} · score ${t.root.overall ?? "–"}${t.root.status === "rejected" ? " · below the bar" : ""}">${i + 1}</button>`)
      .join("")}</div>`;
  }
  function openingsSeg(c) {
    const r = rootOf(c);
    const ms = get()
      .clips.filter((x) => rootOf(x) === r)
      .sort((a, b) => (a.variant_of ? 1 : 0) - (b.variant_of ? 1 : 0) || a.id - b.id);
    if (ms.length < 2) return "";
    return `<div class="seg sm ih-open" role="radiogroup" aria-label="Opening" title="Same moment, different openings">${ms
      .map((m, i) => `<button type="button" role="radio" data-open="${esc(m.id)}" aria-checked="${m.id === c.id}">${String.fromCharCode(65 + i)}</button>`)
      .join("")}</div>`;
  }

  function scoreSec(c) {
    const sc = c.scores || {};
    const rs = c.reasons || {};
    const basis = c.score_basis || {};
    const stale = c.scores_stale ?? c.stale;
    const bars = SCORE_KEYS.map(([k, l]) => {
      const v = sc[k];
      return `<span class="mb" data-mbcap="${esc(l)} ${v ?? "–"}${rs[k] ? " — " + esc(rs[k]) : ""}" aria-label="${esc(l)} ${v ?? "–"}"><b><i style="height:${v == null ? 0 : (clamp(v / 10, 0, 1) * 100).toFixed(0)}%"></i></b></span>`;
    }).join("");
    const rows = SCORE_KEYS.map(([k, l]) => {
      const v = sc[k];
      return `<li><span class="wr-l">${esc(l)}</span><span class="wr-v mono">${v == null ? "–" : (+v).toFixed(v % 1 ? 1 : 0)}</span><span class="wr-b mono">${esc(basis[k] || "model")}</span>${rs[k] ? `<p>${esc(rs[k])}</p>` : ""}</li>`;
    }).join("");
    const notes = [
      c.hook ? ["Opening", c.hook.replace(/^Alt open:\s*/i, "")] : null,
      c.payoff ? ["Payoff", c.payoff] : null,
    ].filter(Boolean);
    return `
      <section class="sec" data-sec="score">
        <div class="score-strip">
          <span class="sh-n mono" title="ClipForge score — editorial estimate, not a view prediction">${c.overall ?? "–"}</span>
          <span class="mbars" aria-label="Score breakdown">${bars}</span>
        </div>
        <p class="mb-cap" data-default="${esc(capDefault(sc))}">${esc(capDefault(sc))}</p>
        ${stale ? `<p class="stale">Scores are from before your edits. <button class="link" type="button" data-act="rescore">Rescore</button></p>` : ""}
        <details class="fold" data-fold="why">
          <summary><span class="fold-t">Why ${c.overall ?? ""}</span><span class="fold-n">editorial estimate, not a view prediction</span>${icon.chev}</summary>
          <ul class="why-rows">${rows}</ul>
          ${notes.length ? `<dl class="why-notes">${notes.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
        </details>
      </section>`;
  }

  function capDefault(sc) {
    const ks = SCORE_KEYS.filter(([k]) => sc[k] != null).map(([k, l]) => [l, sc[k]]);
    if (!ks.length) return "";
    const hi = ks.reduce((a, b) => (b[1] > a[1] ? b : a));
    const lo = ks.reduce((a, b) => (b[1] < a[1] ? b : a));
    return `Strongest ${hi[0].toLowerCase()} ${hi[1]} · weakest ${lo[0].toLowerCase()} ${lo[1]}`;
  }

  function hookSec(c) {
    const ws = words(c);
    const pick = (list, fromEnd) => {
      const seq = fromEnd ? list.slice().reverse() : list;
      const out = [];
      for (let i = 0; i < seq.length && out.length < 14; i++) {
        out.push(seq[i]);
        const nx = seq[i + 1];
        const gap = nx ? (fromEnd ? seq[i].s - nx.e : nx.s - seq[i].e) : 0;
        if (out.length >= 5 && gap > 0.35) break;
      }
      return fromEnd ? out.reverse() : out;
    };
    const inR = wordsBetween(c.words, draft.start_s, draft.end_s);
    const ctx = c.needs_context ? `<p class="hp-ctx">Needs context — ${esc(c.context_note || "viewers may need the setup")}</p>` : "";
    if (!inR.length) return ctx ? `<section class="sec" data-sec="hook">${ctx}</section>` : "";
    const h = pick(inR, false);
    const p = pick(inR, true);
    const line = (lab, arr) => `<div class="hp"><span class="hp-t mono">${tc(arr[0].s)}</span><span class="hp-l">${lab}</span><p>${esc(arr.map((w) => w.t).join(" "))}</p></div>`;
    return `<section class="sec hps" data-sec="hook">${line("Opens", h)}${p[0].s > h[h.length - 1].e ? line("Lands", p) : ""}${ctx}</section>`;
  }

  function words(c) {
    return wordsBetween(c.words, -1, 1e9);
  }

  function transcriptSec(c) {
    const ws = words(c);
    let body;
    if (ws.length) {
      body = ws
        .map((w) => {
          const inR = w.e > draft.start_s + 0.01 && w.s < draft.end_s - 0.01;
          return `<span class="${inR ? "in" : ""}">${esc(w.t.trim())}</span>`;
        })
        .join(" ");
    } else if (c.transcript_excerpt) body = `<span class="in">${esc(c.transcript_excerpt)}</span>`;
    else return "";
    return `<section class="sec" data-sec="tx"><details class="fold" data-fold="tx"><summary><span class="fold-t">Transcript</span>${icon.chev}</summary><p class="tx">${body}</p></details></section>`;
  }

  function trimSec(c, len) {
    const ws = words(c);
    const job = sourceJobs().find((j) => j.id === c.job_id);
    const t = c.target || job?.settings?.target;
    const band = t && t !== "auto" ? t.split("-").map(Number) : null;
    const out = band && (len < band[0] - 0.05 || len > band[1] + 0.05);
    const nud = (k) => `
      <div class="nudge" data-nk="${k}">
        <button class="nb" type="button" data-n="-w" ${ws.length ? "" : 'disabled title="No word timings for this clip"'} aria-label="${k === "start_s" ? "Start" : "End"} one word earlier" title="One word earlier">−word</button>
        <button class="nb" type="button" data-n="-0.1" aria-label="${k === "start_s" ? "Start" : "End"} 0.1 s earlier" title="0.1 s earlier">−0.1</button>
        <span class="mono nv ${Math.abs(draft[k] - c[k]) > 1e-3 ? "moved" : ""}">${tcf(draft[k])}</span>
        <button class="nb" type="button" data-n="+0.1" aria-label="${k === "start_s" ? "Start" : "End"} 0.1 s later" title="0.1 s later">+0.1</button>
        <button class="nb" type="button" data-n="+w" ${ws.length ? "" : 'disabled title="No word timings for this clip"'} aria-label="${k === "start_s" ? "Start" : "End"} one word later" title="One word later">+word</button>
      </div>`;
    return `
      <section class="sec" data-sec="trim">
        <div class="trim">
          <span class="lbl">Start</span>${nud("start_s")}
          <span class="lbl">End</span>${nud("end_s")}
          <span class="lbl">Length</span><span class="mono tl ${out ? "warn" : ""}">${len.toFixed(1)}s${band ? ` <small>target ${band[0]}–${band[1]}</small>` : ""}</span>
        </div>
      </section>`;
  }

  function captionSec(c) {
    if (!draft.captions.length) return "";
    return `
      <section class="sec" data-sec="captions">
        <details class="fold" data-fold="captions" open>
          <summary><span class="lbl">Captions</span><span class="mono fold-n">${draft.captions.length} lines</span>${icon.chev}</summary>
          <ol class="caps">${draft.captions
            .map(
              (x, i) => `<li><span class="mono">${tcf(x.start)}</span><textarea data-cap="${i}" rows="1" spellcheck="true" aria-label="Caption ${i + 1}">${esc(String(x.text))}</textarea></li>`
            )
            .join("")}</ol>
        </details>
      </section>`;
  }

  function frameFor(c, src) {
    const tl = get().timeline;
    const t = c.best_open_s ?? c.start_s;
    if (tl?.frames?.length && String(get().sourceId) === String(c.source_id)) {
      let best = tl.frames[0];
      for (const f of tl.frames) if (Math.abs(f.t - t) < Math.abs(best.t - t)) best = f;
      return best.url;
    }
    return src?.thumb_url || "";
  }

  function cropSec(c, src) {
    const w = src?.width || 16;
    const hgt = src?.height || 9;
    if (hgt >= w) return `<section class="sec" data-sec="crop"><span class="lbl">Crop</span><p class="muted">Source is already vertical — scaled, not cropped.</p></section>`;
    const img = frameFor(c, src);
    const track = c.crop?.track || [];
    return `
      <section class="sec" data-sec="crop">
        <div class="crop-head">
          <span class="lbl">Crop</span>
          <div class="seg sm" role="radiogroup" aria-label="Crop mode">
            <button type="button" role="radio" data-crop="auto" aria-checked="${draft.crop.mode === "auto"}">Auto</button>
            <button type="button" role="radio" data-crop="manual" aria-checked="${draft.crop.mode === "manual"}">Manual</button>
          </div>
        </div>
        <div class="crop-frame" style="aspect-ratio:${w}/${hgt}" data-ar="${(hgt * 9) / 16 / w}">
          ${img ? `<img src="${esc(img)}" alt="" draggable="false">` : ""}
          ${track.length > 1 && draft.crop.mode === "auto" ? `<svg class="crop-track" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${track
            .map(([t, x]) => `${(x * 100).toFixed(2)},${(((t - track[0][0]) / Math.max(0.01, track[track.length - 1][0] - track[0][0])) * 100).toFixed(2)}`)
            .join(" ")}"/></svg>` : ""}
          <div class="crop-win ${draft.crop.mode}"></div>
        </div>
        ${draft.crop.mode === "manual" ? `<input class="crop-x" type="range" min="0" max="1" step="0.005" value="${draft.crop.x}" aria-label="Crop position">` : `<p class="muted sm">${track.length ? `Follows the face · ${track.length} keyframes` : "Centred — no face found"}</p>`}
      </section>`;
  }
  function drawCropWindow() {
    const f = root.querySelector(".crop-frame");
    if (!f) return;
    const ar = Number(f.dataset.ar) || 0.316;
    const win = f.querySelector(".crop-win");
    const c = clipNow();
    let x = draft.crop.x;
    if (draft.crop.mode === "auto") {
      const tr = c?.crop?.track || [];
      if (tr.length) {
        const xs = tr.map((p) => p[1]).sort((a, b) => a - b);
        x = xs[Math.floor(xs.length / 2)];
      } else x = c?.crop?.x ?? 0.5;
    }
    const left = clamp(x - ar / 2, 0, 1 - ar);
    win.style.left = left * 100 + "%";
    win.style.width = ar * 100 + "%";
  }

  function presetSec() {
    return `
      <section class="sec row" data-sec="preset">
        <span class="lbl">Caption style</span>
        <div class="select sm"><select data-k="caption_preset" aria-label="Caption preset">${PRESETS.map(([k, l]) => `<option value="${k}" ${k === draft.caption_preset ? "selected" : ""}>${l}</option>`).join("")}</select>${icon.chev}</div>
      </section>`;
  }

  function concernsSec(c, rights, canExport) {
    const cs = c.concerns || [];
    return `
      <section class="sec" data-sec="concerns">
        <div class="rights"><span class="lbl">Rights</span><span><span class="dot t-${rightsTone(rights)}"></span>${esc(RIGHTS_LABEL[rights] || rights)}</span></div>
        ${canExport ? "" : `<p class="blocked">Export is blocked until ${esc(c.creator_name || "this creator")}'s rights are owned, licensed or permission granted. <a href="#/creators">Set rights</a></p>`}
        ${cs.length ? `<ul class="concerns">${cs.map((x) => `<li>${esc(typeof x === "string" ? x : x.text || x.kind || JSON.stringify(x))}</li>`).join("")}</ul>` : ""}
      </section>`;
  }

  function qcSec(c) {
    const ch = c.qc?.checks || [];
    if (!ch.length) return "";
    const bad = ch.filter((x) => !x.pass);
    return `
      <section class="sec" data-sec="qc">
        <details class="fold" data-fold="qc" ${bad.length ? "open" : ""}>
          <summary><span class="lbl">QC</span><span class="mono fold-n">${ch.length - bad.length}/${ch.length} passed</span>${icon.chev}</summary>
          <ul class="qc">${ch.map((x) => `<li class="${x.pass ? "" : "bad"}"><span class="dot t-${x.pass ? "ok" : "bad"}"></span><span>${esc(x.name)}</span>${x.detail ? `<small>${esc(x.detail)}</small>` : ""}</li>`).join("")}</ul>
        </details>
      </section>`;
  }

  // ---- interactions
  function nudge(k, n) {
    const c = clipNow();
    const ws = words(c);
    let v = draft[k];
    if (n === "-0.1" || n === "+0.1") v += n === "-0.1" ? -0.1 : 0.1;
    else if (ws.length) {
      const edges = ws.map((w) => (k === "start_s" ? w.s : w.e)).sort((a, b) => a - b);
      if (n === "-w") v = [...edges].reverse().find((e) => e < v - 0.02) ?? v;
      else v = edges.find((e) => e > v + 0.02) ?? v;
    }
    v = Math.max(0, Math.round(v * 1000) / 1000);
    if (k === "start_s" && v >= draft.end_s - 1) return;
    if (k === "end_s" && v <= draft.start_s + 1) return;
    draft[k] = v;
    render();
  }

  root.addEventListener("click", async (e) => {
    const c = clipNow();
    if (!c) return;
    if (e.target.closest("[data-close]")) return setInspector(false);
    const op = e.target.closest("[data-open]");
    if (op) return selectClip(/^\d+$/.test(op.dataset.open) ? Number(op.dataset.open) : op.dataset.open);
    const nb = e.target.closest("[data-n]");
    if (nb) return nudge(nb.closest("[data-nk]").dataset.nk, nb.dataset.n);
    const cr = e.target.closest("[data-crop]");
    if (cr) {
      draft.crop.mode = cr.dataset.crop;
      if (cr.dataset.crop === "manual" && c.crop?.track?.length) {
        const xs = c.crop.track.map((p) => p[1]).sort((a, b) => a - b);
        draft.crop.x = xs[Math.floor(xs.length / 2)];
      }
      return render();
    }
    const a = e.target.closest("[data-act]");
    if (!a || a.disabled) return;
    const act = a.dataset.act;
    if (act === "more") {
      moreOpen = !moreOpen;
      return render();
    }
    moreOpen = false;
    if (act === "discard") {
      draft = freshDraft(c);
      return render();
    }
    if (act === "save-render") {
      pending = true;
      render();
      await rerender(c.id, edits(c));
      pending = false;
      draft = freshDraft({ ...c, ...edits(c) });
      return render();
    }
    if (act === "rescore") {
      try {
        await api.rescore(c.id);
        notify({ tone: "live", title: "Rescoring" });
        loadClipDetail(c.id);
      } catch (er) {
        fail(er, "Couldn't rescore");
      }
      return;
    }
    if (act === "render") return rerender(c.id);
    if (act === "approve") return setStatus([c.id], "approved");
    if (act === "reject") return setStatus([c.id], "rejected");
    if (act === "unreject") return setStatus([c.id], "candidate");
    if (act === "export") return exportClips([c.id]);
    if (act === "variants") return makeVariants(c.id);
    if (act === "publish") {
      try {
        await api.publish(c.id);
        notify({ tone: "ok", title: "Published" });
      } catch (er) {
        fail(er, "Publish blocked");
      }
    }
  });
  root.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.cap != null) {
      draft.captions[Number(t.dataset.cap)].text = t.value;
      grow(t);
    }
    else if (t.dataset.k === "title") draft.title = t.value;
    else if (t.classList.contains("crop-x")) {
      draft.crop.x = Number(t.value);
      drawCropWindow();
      return;
    } else return;
    refreshFoot();
  });
  root.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.k === "caption_preset") {
      draft.caption_preset = t.value;
      render();
    } else if (t.classList.contains("crop-x")) render();
  });
  root.addEventListener("focusout", () => {
    setTimeout(() => {
      if (deferred && !root.contains(document.activeElement)) {
        deferred = false;
        render();
      }
    }, 0);
  });
  // drag the crop window directly
  root.addEventListener("pointerdown", (e) => {
    const f = e.target.closest(".crop-frame");
    if (!f || draft.crop.mode !== "manual") return;
    const ar = Number(f.dataset.ar) || 0.316;
    const move = (ev) => {
      const r = f.getBoundingClientRect();
      draft.crop.x = clamp((ev.clientX - r.left) / r.width, ar / 2, 1 - ar / 2);
      const sl = root.querySelector(".crop-x");
      if (sl) sl.value = draft.crop.x;
      drawCropWindow();
    };
    move(e);
    f.setPointerCapture(e.pointerId);
    f.addEventListener("pointermove", move);
    f.addEventListener(
      "pointerup",
      () => {
        f.removeEventListener("pointermove", move);
        render();
      },
      { once: true }
    );
  });

  root.addEventListener("pointerover", (e) => {
    const b = e.target.closest(".mb");
    const cap = root.querySelector(".mb-cap");
    if (cap) cap.textContent = b ? b.dataset.mbcap : cap.dataset.default;
  });
  root.addEventListener("pointerleave", () => {
    const cap = root.querySelector(".mb-cap");
    if (cap) cap.textContent = cap.dataset.default;
  });
  root.addEventListener("toggle", (e) => e.target.matches?.("details") && e.target.open && e.target.querySelectorAll("textarea[data-cap]").forEach(grow), true);
  document.addEventListener("pointerdown", (e) => {
    if (moreOpen && !e.target.closest(".more-menu, [data-act=more]")) {
      moreOpen = false;
      render();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && moreOpen) {
      moreOpen = false;
      render();
    }
  });

  function refreshFoot() {
    // cheap: re-render the footer only, keep the focused input alive
    const c = clipNow();
    const foot = root.querySelector(".insp-foot");
    if (!foot || !c) return;
    const tmp = document.createElement("div");
    const dk = dirtyKeys(c);
    const src = get().sources.find((x) => x.id === c.source_id);
    const canExport = RIGHTS_OK.includes(c.rights_status || src?.rights_status || "unknown");
    tmp.innerHTML = dk.length
      ? `<span class="if-dirty"><span class="dot t-live"></span>${dk.length} unsaved</span><button class="btn ghost sm" type="button" data-act="discard">Discard</button><span class="if-gap"></span>${actions(c, true, canExport)}`
      : `<button class="btn ghost sm" type="button" data-act="more">More</button><span class="if-gap"></span>${actions(c, false, canExport)}`;
    foot.replaceChildren(...tmp.childNodes);
  }

  let deferred = false;
  const guarded = () => {
    const a = document.activeElement;
    if (a && root.contains(a) && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
      deferred = true;
      return;
    }
    render();
  };
  const off = on(["clip", "clipId", "clips", "inspector", "integrations", "timeline", "sources"], guarded);
  render();
  return off;
}

export function inspectorAction(act) {
  document.querySelector(`.insp [data-act="${act}"]`)?.click();
}
