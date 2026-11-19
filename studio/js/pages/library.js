// Library — every imported source with status; import from disk or an authorised URL.
import { get, on } from "../store.js";
import { api } from "../api.js";
import { esc, tc, ago, RIGHTS_LABEL, rightsTone, icon } from "../util.js";
import { loadSources, selectSource, fail, notify } from "../actions.js";
import { openImport } from "../components/importer.js";
import { srcLabel, srcTone } from "../components/sourcebar.js";

export function mount(el) {
  el.innerHTML = `
    <section class="pg">
      <header class="pg-head">
        <h1>Library</h1>
        <form class="url-imp">
          <input name="url" type="url" placeholder="Video URL — downloads only if the creator's rights are cleared" spellcheck="false">
          <div class="select sm"><select name="creator" aria-label="Creator"></select>${icon.chev}</div>
          <button class="btn sm" type="submit">Fetch</button>
        </form>
        <button class="btn primary" type="button" data-import>Import video</button>
      </header>
      <div class="pg-body"><div class="tbl-wrap"></div></div>
    </section>`;
  const wrap = el.querySelector(".tbl-wrap");
  const form = el.querySelector(".url-imp");

  function render() {
    const s = get();
    form.creator.innerHTML = s.creators.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("") || `<option value="">No creators</option>`;
    const rows = s.sources.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    if (!rows.length) {
      wrap.innerHTML = `<div class="pg-empty"><p>Nothing imported yet.</p><button class="btn" type="button" data-import>Import video</button></div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="tbl">
        <thead><tr><th></th><th>Title</th><th>Creator</th><th class="num">Length</th><th>Origin</th><th>Rights</th><th>Status</th><th class="num">Clips</th><th>Added</th></tr></thead>
        <tbody>${rows
          .map(
            (x) => `<tr data-id="${esc(x.id)}" tabindex="0">
              <td class="th-cell"><span class="src-thumb">${x.thumb_url ? `<img src="${esc(x.thumb_url)}" alt="" loading="lazy">` : ""}</span></td>
              <td><b>${esc(x.title || "Untitled")}</b>${x.width ? `<small class="mono">${x.width}×${x.height}${x.fps ? " · " + Math.round(x.fps) + " fps" : ""}${x.has_audio === 0 ? " · no audio" : ""}</small>` : ""}</td>
              <td>${esc(x.creator_name || get().creators.find((c) => c.id === x.creator_id)?.name || "—")}</td>
              <td class="num mono">${tc(x.duration_s)}</td>
              <td class="origin mono" title="${esc(x.origin_ref || "")}">${esc(x.origin || "")}${x.origin_ref ? " · " + esc(x.origin_ref.split("/").pop()) : ""}</td>
              <td><span class="dot t-${rightsTone(x.rights_status)}"></span> ${esc(RIGHTS_LABEL[x.rights_status] || x.rights_status || "—")}</td>
              <td><span class="dot t-${srcTone(x.status)}"></span> ${esc(srcLabel(x.status))}</td>
              <td class="num mono">${x.clip_counts ? Object.entries(x.clip_counts).filter(([k]) => k !== "rejected").reduce((a, [, n]) => a + n, 0) : "—"}</td>
              <td class="muted">${esc(ago(x.created_at))}</td>
            </tr>`
          )
          .join("")}</tbody>
      </table>`;
  }

  el.addEventListener("click", (e) => {
    if (e.target.closest("[data-import]")) openImport();
    const tr = e.target.closest("tr[data-id]");
    if (tr) {
      selectSource(/^\d+$/.test(tr.dataset.id) ? Number(tr.dataset.id) : tr.dataset.id);
      location.hash = "#/studio";
    }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = form.url.value.trim();
    if (!url) return form.url.focus();
    if (!form.creator.value) return fail({ message: "Add a creator first", detail: "URL imports need a creator with cleared rights." });
    try {
      const src = await api.importUrl({ url, creator_id: Number(form.creator.value) || form.creator.value });
      await loadSources();
      notify({ tone: "ok", title: "Download started", detail: src?.title || url });
      form.url.value = "";
    } catch (er) {
      fail(er, er.status === 403 ? "Download blocked" : "Couldn't fetch");
    }
  });

  const off = on(["sources", "creators"], render);
  render();
  loadSources();
  return off;
}
