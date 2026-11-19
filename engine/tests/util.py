"""Test helpers: synthetic media via ffmpeg lavfi and macOS `say`, temp servers."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
import media  # noqa: E402

FF = media.FF
PY = sys.executable

SPEECH = ("Seven years ago I quit my job with no plan at all. Everyone told me it was the worst decision of my life. "
          "My father said I would be back within a month. I was not back in a month. I spent the first year broke, "
          "sleeping on a friend's couch and learning to edit video at night. Then one client called me on a Tuesday. "
          "She needed a video in two days for a launch. I said yes before I knew how I would do it. "
          "That video got two million views. And that is how the whole studio started. ")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def lavfi_video(out, dur=4, size="1080x1920", color=None, tone=True, black_from=None, black_to=None, silent=False,
                volume_db=-14):
    """Synthetic video. color=None → testsrc2 pattern. black_from/to inserts a black section."""
    vsrc = f"color=c={color}:s={size}:r=30:d={dur}" if color else f"testsrc2=s={size}:r=30:d={dur}"
    vf = []
    if black_from is not None:
        vf.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{black_from},{black_to})'")
    args = [FF, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", vsrc]
    if tone:
        asrc = "anullsrc=r=48000:cl=stereo" if silent else f"sine=f=440:r=48000:d={dur}"
        args += ["-f", "lavfi", "-i", asrc]
    if vf:
        args += ["-vf", ",".join(vf)]
    if tone:
        args += ["-af", "volume=0" if silent else f"loudnorm=I={volume_db}:TP=-1:LRA=11", "-c:a", "aac", "-ar", "48000", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(dur), out]
    subprocess.run(args, check=True)
    return out


def speech_video(out, repeats=2, size="1280x720", voice=None):
    """Talking-style test video: testsrc2 picture + real synthetic speech from macOS `say` (offline)."""
    td = tempfile.mkdtemp()
    aiff = os.path.join(td, "s.aiff")
    cmd = ["say", "-o", aiff]
    if voice:
        cmd += ["-v", voice]
    subprocess.run(cmd + [SPEECH * repeats], check=True)
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", "-i", aiff, "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30",
                    "-map", "1:v", "-map", "0:a", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", out], check=True)
    shutil.rmtree(td, ignore_errors=True)
    return out


class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return r.status, (json.loads(b) if b and r.headers.get("Content-Type", "").startswith("application/json") else b), r.headers
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b), e.headers
            except ValueError:
                return e.code, b, e.headers

    def get(self, p):
        return self.call("GET", p)

    def post(self, p, body=None):
        return self.call("POST", p, body or {})

    def patch(self, p, body):
        return self.call("PATCH", p, body)


def start_server(data_dir, port=None, harness=False, env=None):
    port = port or free_port()
    e = dict(os.environ)
    e["CLIPFORGE_DATA"] = data_dir
    e.update(env or {})
    script = os.path.join(ENGINE, "tests", "harness_engine.py") if harness else os.path.join(ENGINE, "server.py")
    args = [PY, script, str(port), data_dir] if harness else [PY, script, "--port", str(port), "--data", data_dir]
    log = open(os.path.join(data_dir, "server.log"), "ab")
    p = subprocess.Popen(args, env=e, stdout=log, stderr=log)
    api = Api(port)
    for _ in range(100):
        try:
            if api.get("/api/ping")[0] == 200:
                return p, api
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    p.kill()
    raise RuntimeError("server did not start: " + open(os.path.join(data_dir, "server.log")).read()[-2000:])


def wait(pred, timeout=300, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise TimeoutError("condition not met in %ss" % timeout)
