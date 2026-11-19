// Import: drop/upload a file (real byte progress), or import a file/folder path on this Mac.
import { get, on } from "../store.js";
import { api, upload } from "../api.js";
import { esc, icon, RIGHTS_LABEL, RIGHTS_EXPLAIN } from "../util.js";
import { loadSources, loadCreators, selectSource, fail, notify, refreshAll } from "../actions.js";

const VIDEO_EXT = /\.(mp4|mov|m4v|mkv|webm|avi|mts|ts|flv)$/i;

export function renderDropbox(el, { onDone } = {}) {
  el.classList.add("dropbox");
  el.innerHTML = `
    <label class="db-zone" tabindex="0">
      <input type="file" accept="video/*,.mkv,.mov,.mp4,.m4v,.webm" hidden>
      <span class="db-title">Drop a video or choose a folder</span>
      <span class="db-sub">MP4, MOV, MKV · any length</span>
      <span class="db-prog" hidden><i></i><em class="mono"></em></span>
    </label>
    <form class="db-path">
      <input type="text" name="path" placeholder="Or paste a path — /Users/you/Movies/stream.mp4 or a folder" spellcheck="false" autocomplete="off">
      <button class="btn sm" type="submit">Import</button>
    </form>
    <div class="db-creator"></div>`;
  const zone = el.querySelector(".db-zone");
  const file = el.querySelector("input[type=file]");
  const prog = el.querySelector(".db-prog");
  const form = el.querySelector(".db-path");
  const who = el.querySelector(".db-creator");

  function renderWho() {
    const cs = get().creators;
    if (cs.length) {
      const last = Number(sessionStorage.getItem("cf.lastCreator")) || cs[0].id;
      who.innerHTML = `
        <span class="tb-label">Creator</span>
        <div class="select sm"><select name="creator">${cs
          .map((c) => `<option value="${c.id}" ${c.id === last ? "selected" : ""}>${esc(c.name)} — ${esc(RIGHTS_LABEL[c.rights_status] || c.rights_status)}</option>`)
          .join("")}</select>${icon.chev}</div>`;
    } else {
      who.innerHTML = `
        <span class="tb-label">Whose video?</span>
        <input type="text" name="cname" placeholder="Creator name" value="">
        <div class="select sm"><select name="crights">
          ${["owned", "permission_granted", "licensed", "pending", "unknown"].map((r) => `<option value="${r}">${esc(RIGHTS_LABEL[r])}</option>`).join("")}
        </select>${icon.chev}</div>
        <small class="db-rights"></small>`;
      const rs = who.querySelector("[name=crights]");
      const ex = who.querySelector(".db-rights");
      const upd = () => (ex.textContent = RIGHTS_EXPLAIN[rs.value]);
      rs.addEventListener("change", upd);
      upd();
    }
  }
  renderWho();
  const off = on(["creators"], renderWho);

  async function creatorId() {
    const sel = who.querySelector("[name=creator]");
    if (sel) {
      sessionStorage.setItem("cf.lastCreator", sel.value);
      return Number(sel.value) || sel.value;
    }
    const name = who.querySelector("[name=cname]").value.trim();
    if (!name) {
      who.querySelector("[name=cname]").focus();
      throw Object.assign(new Error("Name the creator first"), { detail: "Every source belongs to a creator — that's where rights live." });
    }
    const c = await api.createCreator({ name, platform: "local", source_method: "upload", rights_status: who.querySelector("[name=crights]").value });
    await loadCreators();
    return c.id;
  }

  async function doUpload(f) {
    if (!f) return;
    let cid;
    try {
      cid = await creatorId();
    } catch (e) {
      return fail(e);
    }
    prog.hidden = false;
    const bar = prog.querySelector("i");
    const pct = prog.querySelector("em");
    zone.classList.add("busy");
    try {
      const src = await upload(f, cid, (p) => {
        bar.style.transform = `scaleX(${p})`;
        pct.textContent = Math.round(p * 100) + "%";
      });
      await loadSources();
      notify({ tone: "ok", title: "Imported", detail: src?.title || f.name });
      if (src?.id != null) await selectSource(src.id);
      onDone?.(src);
    } catch (e) {
      fail(e, "Upload failed");
    } finally {
      prog.hidden = true;
      zone.classList.remove("busy");
    }
  }

  file.addEventListener("change", () => doUpload(file.files[0]));
  zone.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), file.click()));
  ["dragenter", "dragover"].forEach((t) =>
    zone.addEventListener(t, (e) => {
      e.preventDefault();
      zone.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("video/") || VIDEO_EXT.test(x.name));
    if (f) doUpload(f);
    else notify({ tone: "bad", title: "That isn't a video file", detail: "Drop an MP4, MOV, MKV or WEBM." });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = form.path.value.trim().replace(/^['"]|['"]$/g, "");
    if (!path) return form.path.focus();
    let cid;
    try {
      cid = await creatorId();
    } catch (er) {
      return fail(er);
    }
    try {
      if (VIDEO_EXT.test(path)) {
        const src = await api.importPath({ path, creator_id: cid });
        await loadSources();
        notify({ tone: "ok", title: "Imported", detail: src?.title || path.split("/").pop() });
        form.path.value = "";
        if (src?.id != null) await selectSource(src.id);
        onDone?.(src);
      } else {
        await api.updateCreator(cid, { source_method: "folder", folder_path: path });
        const r = await api.scanCreator(cid);
        await refreshAll();
        if (r?.status === "blocked") throw Object.assign(new Error("Folder scan blocked"), { detail: r.reason || "", fix: r.fix || "" });
        const n = Array.isArray(r?.added) ? r.added.length : r?.added ?? null;
        notify({ tone: "ok", title: "Folder linked", detail: n != null ? `${n} new video${n === 1 ? "" : "s"} found` : path });
        form.path.value = "";
        onDone?.();
      }
    } catch (er) {
      fail(er, "Import failed");
    }
  });
  return off;
}

let sheet;
export function openImport() {
  closeImport();
  sheet = document.createElement("div");
  sheet.className = "sheet-wrap";
  sheet.innerHTML = `<div class="sheet" role="dialog" aria-label="Import video"><header><b>Import video</b><button class="icon-btn" type="button" aria-label="Close">✕</button></header><div class="sheet-body"></div></div>`;
  document.body.appendChild(sheet);
  const off = renderDropbox(sheet.querySelector(".sheet-body"), { onDone: closeImport });
  sheet._off = off;
  sheet.addEventListener("click", (e) => {
    if (e.target === sheet || e.target.closest(".icon-btn")) closeImport();
  });
  sheet.querySelector(".db-zone").focus();
}
export function closeImport() {
  if (sheet) {
    sheet._off?.();
    sheet.remove();
    sheet = null;
  }
}
