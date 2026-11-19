// Recording layout: when the window is portrait-ish (his OBS slot is ~800×926 → 1080×1250), lay out for
// 800×926 and scale the whole page to the window width, so every size records identically.
const BASE_W = 800;
export function fit() {
  const w = innerWidth;
  const h = innerHeight;
  const rec = h / w >= 1 && w <= 1500;
  const root = document.documentElement;
  root.classList.toggle("rec", rec);
  const z = rec ? w / BASE_W : 1;
  root.style.zoom = z === 1 ? "" : String(z);
  const desk = document.querySelector(".desk");
  if (desk) desk.style.height = rec ? h / z + "px" : "";
}
