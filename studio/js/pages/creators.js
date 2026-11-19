// Creators — table + add/edit drawer. Rights live here.
import { get, on } from "../store.js";
import { api } from "../api.js";
import { esc, ago, RIGHTS_LABEL, RIGHTS_EXPLAIN, rightsTone, icon } from "../util.js";
import { loadCreators, loadSources, fail, notify, refreshAll } from "../actions.js";

const PLATFORMS = [["local", "Local files"], ["youtube", "YouTube"], ["twitch", "Twitch"], ["kick", "Kick"], ["tiktok", "TikTok"], ["other", "Other"]];
const METHODS = [
  ["upload", "Upload by hand"],
  ["folder", "Watch a folder"],
  ["youtube_api", "YouTube API"],
  ["twitch_api", "Twitch API"],
  ["ytdlp_authorized", "Download (authorised only)"],
];
const RIGHTS = ["owned", "licensed", "permission_granted", "pending", "unknown", "denied"];

export function mount(el) {
  el.innerHTML = `
    <section class="pg">
      <header class="pg-head"><h1>Creators</h1><button class="btn primary" type="button" data-new>Add creator</button></header>
      <div class="pg-body"><div class="tbl-wrap"></div></div>
      <aside class="drawer" hidden></aside>
    </section>`;
  const wrap = el.querySelector(".tbl-wrap");
  const drawer = el.querySelector(".drawer");
  let editing = null;

  function render() {
    const s = get();
    const counts = {};
    s.sources.forEach((x) => (counts[x.creator_id] = (counts[x.creator_id] || 0) + 1));
    if (!s.creators.length) {
      wrap.innerHTML = `<div class="pg-empty"><p>No creators yet.</p><p class="muted">A creator holds the rights status for every video you import from them.</p></div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="tbl">
        <thead><tr><th>Name</th><th>Platform</th><th>Source</th><th>Monitoring</th><th class="num">Videos</th><th>Rights</th><th>Last scan</th></tr></thead>
        <tbody>${s.creators
          .map(
            (c) => `<tr data-id="${esc(c.id)}" class="${editing && editing.id === c.id ? "on" : ""}" tabindex="0">
              <td><b>${esc(c.name)}</b>${c.channel_url ? `<small class="mono">${esc(c.channel_url)}</small>` : c.folder_path ? `<small class="mono">${esc(c.folder_path)}</small>` : ""}</td>
              <td>${esc((PLATFORMS.find((p) => p[0] === c.platform) || [, c.platform])[1])}</td>
              <td>${esc((METHODS.find((m) => m[0] === c.source_method) || [, c.source_method || "—"])[1])}</td>
              <td>${c.monitoring ? `<span class="dot t-ok"></span> On` : `<span class="muted">Off</span>`}</td>
              <td class="num mono">${counts[c.id] || 0}</td>
              <td><span class="dot t-${rightsTone(c.rights_status)}"></span> ${esc(RIGHTS_LABEL[c.rights_status] || c.rights_status || "Unknown")}</td>
              <td class="muted">${c.last_scan_at ? esc(ago(c.last_scan_at)) : "—"}</td>
            </tr>`
          )
          .join("")}</tbody>
      </table>`;
  }

  function open(c) {
    editing = c || { name: "", platform: "local", channel_url: "", monitoring: 0, source_method: "upload", folder_path: "", rights_status: "owned", rights_note: "" };
    drawer.hidden = false;
    const r = editing.rights_status || "unknown";
    drawer.innerHTML = `
      <form class="dr-form">
        <header><b>${editing.id ? esc(editing.name) : "New creator"}</b><button class="icon-btn" type="button" data-close aria-label="Close">${icon.x}</button></header>
        <div class="dr-body">
          <label class="fld"><span>Name</span><input name="name" value="${esc(editing.name)}" required></label>
          <div class="fld-row">
            <label class="fld"><span>Platform</span><div class="select"><select name="platform">${PLATFORMS.map(([k, l]) => `<option value="${k}" ${k === editing.platform ? "selected" : ""}>${l}</option>`).join("")}</select>${icon.chev}</div></label>
            <label class="fld"><span>How videos arrive</span><div class="select"><select name="source_method">${METHODS.map(([k, l]) => `<option value="${k}" ${k === editing.source_method ? "selected" : ""}>${l}</option>`).join("")}</select>${icon.chev}</div></label>
          </div>
          <label class="fld"><span>Channel URL</span><input name="channel_url" type="url" value="${esc(editing.channel_url || "")}" placeholder="https://"></label>
          <label class="fld"><span>Folder on this Mac</span><input name="folder_path" class="mono" value="${esc(editing.folder_path || "")}" placeholder="/Users/you/Movies/Streams"></label>
          <label class="toggle"><input type="checkbox" name="monitoring" ${editing.monitoring ? "checked" : ""}><span>Monitor for new videos</span></label>
          <fieldset class="rights-pick">
            <legend>Rights</legend>
            ${RIGHTS.map(
              (k) => `<label class="rp ${k === r ? "on" : ""}"><input type="radio" name="rights_status" value="${k}" ${k === r ? "checked" : ""}><span class="dot t-${rightsTone(k)}"></span><span class="rp-t"><b>${esc(RIGHTS_LABEL[k])}</b><small>${esc(RIGHTS_EXPLAIN[k])}</small></span></label>`
            ).join("")}
          </fieldset>
          <label class="fld"><span>Rights note</span><textarea name="rights_note" rows="2" placeholder="Where the permission lives — email, contract, DM">${esc(editing.rights_note || "")}</textarea></label>
        </div>
        <footer>
          ${editing.id ? `<button class="btn ghost sm" type="button" data-del>Delete</button>` : ""}
          ${editing.id && (editing.source_method === "folder" || /_api$/.test(editing.source_method || "")) ? `<button class="btn sm" type="button" data-scan>Scan now</button>` : ""}
          <span class="if-gap"></span>
          <button class="btn primary" type="submit">${editing.id ? "Save" : "Add creator"}</button>
        </footer>
      </form>`;
    drawer.querySelector("[name=name]").focus();
    render();
  }
  function close() {
    editing = null;
    drawer.hidden = true;
    drawer.innerHTML = "";
    if (location.hash.includes("new=1")) history.replaceState(null, "", "#/creators");
    render();
  }

  drawer.addEventListener("change", (e) => {
    if (e.target.name === "rights_status") drawer.querySelectorAll(".rp").forEach((l) => l.classList.toggle("on", l.querySelector("input").checked));
  });
  drawer.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close]")) return close();
    if (e.target.closest("[data-del]")) {
      if (!confirm(`Delete ${editing.name}? Their imported sources stay on disk.`)) return;
      try {
        await api.deleteCreator(editing.id);
        await refreshAll();
        close();
      } catch (er) {
        fail(er, "Couldn't delete");
      }
    }
    if (e.target.closest("[data-scan]")) {
      try {
        const r = await api.scanCreator(editing.id);
        await Promise.all([loadCreators(), loadSources()]);
        if (r?.status === "blocked") throw Object.assign(new Error("Scan blocked"), { detail: r.reason || "", fix: r.fix || "" });
        const n = Array.isArray(r?.added) ? r.added.length : r?.added;
        notify({ tone: "ok", title: "Scan finished", detail: n != null ? `${n} new` : "" });
      } catch (er) {
        fail(er, "Scan blocked");
      }
    }
  });
  drawer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = {
      name: f.name.value.trim(),
      platform: f.platform.value,
      source_method: f.source_method.value,
      channel_url: f.channel_url.value.trim(),
      folder_path: f.folder_path.value.trim(),
      monitoring: f.monitoring.checked ? 1 : 0,
      rights_status: f.rights_status.value,
      rights_note: f.rights_note.value.trim(),
    };
    try {
      if (editing.id) await api.updateCreator(editing.id, body);
      else await api.createCreator(body);
      await Promise.all([loadCreators(), loadSources()]);
      notify({ tone: "ok", title: editing.id ? "Saved" : "Creator added", detail: body.name });
      close();
    } catch (er) {
      fail(er, "Couldn't save");
    }
  });
  wrap.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) open(get().creators.find((c) => String(c.id) === tr.dataset.id));
  });
  wrap.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr && e.key === "Enter") open(get().creators.find((c) => String(c.id) === tr.dataset.id));
  });
  el.querySelector("[data-new]").addEventListener("click", () => open(null));

  const off = on(["creators", "sources"], render);
  render();
  loadCreators();
  if (location.hash.includes("new=1")) open(null);
  return off;
}
