// Sound cues, synthesized with WebAudio (no files). OFF by default; the choice is remembered per browser.
let ctx = null;
let on = false;
try { on = localStorage.getItem("clipforge.v2.cues") === "1"; } catch {}
const subs = new Set();

export const cuesOn = () => on;
export function setCues(v) {
  on = !!v;
  try { localStorage.setItem("clipforge.v2.cues", on ? "1" : "0"); } catch {}
  if (on) ac(); // unlock on the user's click
  subs.forEach((f) => f(on));
}
export const onCues = (f) => (subs.add(f), () => subs.delete(f));

function ac() {
  if (!ctx) {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    ctx = new C();
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}
function env(g, t, peak, attack, release) {
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(peak, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + attack + release);
}

/** soft tick — a moment was found */
export function tick() {
  if (!on) return;
  const a = ac();
  if (!a) return;
  const t = a.currentTime;
  const o = a.createOscillator();
  const g = a.createGain();
  o.type = "sine";
  o.frequency.setValueAtTime(1500, t);
  o.frequency.exponentialRampToValueAtTime(950, t + 0.05);
  env(g, t, 0.16, 0.004, 0.07);
  o.connect(g).connect(a.destination);
  o.start(t);
  o.stop(t + 0.1);
}

/** whoosh — a clip landed */
export function whoosh() {
  if (!on) return;
  const a = ac();
  if (!a) return;
  const t = a.currentTime;
  const len = 0.38;
  const buf = a.createBuffer(1, Math.ceil(a.sampleRate * len), a.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
  const src = a.createBufferSource();
  src.buffer = buf;
  const f = a.createBiquadFilter();
  f.type = "bandpass";
  f.Q.value = 0.9;
  f.frequency.setValueAtTime(380, t);
  f.frequency.exponentialRampToValueAtTime(2600, t + len * 0.85);
  const g = a.createGain();
  env(g, t, 0.22, 0.06, len - 0.06);
  src.connect(f).connect(g).connect(a.destination);
  src.start(t);
  src.stop(t + len);
}

/** one short chime — the run finished */
export function chime() {
  if (!on) return;
  const a = ac();
  if (!a) return;
  const t = a.currentTime;
  [[880, 0], [1318.5, 0.09], [1760, 0.18]].forEach(([hz, dt]) => {
    const o = a.createOscillator();
    const g = a.createGain();
    o.type = "sine";
    o.frequency.value = hz;
    env(g, t + dt, 0.11, 0.008, 0.9);
    o.connect(g).connect(a.destination);
    o.start(t + dt);
    o.stop(t + dt + 1);
  });
}
