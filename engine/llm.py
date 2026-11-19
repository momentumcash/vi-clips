"""LLM providers. Default `claude_cli` shells out to `claude -p` (the owner's Claude Code login). `anthropic_api` is used only
when ANTHROPIC_API_KEY is set and the provider is selected. No fake provider exists here — tests bring their own.

complete_json(): ask for strict JSON, validate, re-ask ONCE with the validation errors, then fail honestly.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

TIMEOUT_S = 15 * 60


class LLMError(Exception):
    pass


class Cancelled(Exception):
    pass


class ClaudeCLI:
    name = "claude_cli"

    def __init__(self, model=""):
        self.model = model or ""
        self.bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        if not os.path.exists(self.bin):
            raise LLMError("claude CLI not found — install Claude Code or set provider anthropic_api with ANTHROPIC_API_KEY")

    def complete(self, prompt, system, cancel=None):
        env = dict(os.environ)
        import getpass
        user = env.get("USER") or getpass.getuser()
        env["USER"] = user
        env["LOGNAME"] = env.get("LOGNAME") or user
        env.setdefault("HOME", os.path.expanduser("~"))
        cmd = [self.bin, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]
        with tempfile.TemporaryFile() as fin, tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
            fin.write(prompt.encode())
            fin.seek(0)
            p = subprocess.Popen(cmd, stdin=fin, stdout=fout, stderr=ferr, env=env, cwd=tempfile.gettempdir(),
                                 start_new_session=True)
            t0 = time.time()
            while p.poll() is None:
                if cancel is not None and cancel.is_set():
                    p.kill()
                    raise Cancelled()
                if time.time() - t0 > TIMEOUT_S:
                    p.kill()
                    raise LLMError(f"claude -p timed out after {TIMEOUT_S // 60} min")
                time.sleep(0.25)
            fout.seek(0)
            ferr.seek(0)
            out = fout.read().decode("utf-8", "replace")
            err = ferr.read().decode("utf-8", "replace")
        try:
            j = json.loads(out)
        except ValueError:
            raise LLMError(f"claude -p returned non-JSON (rc {p.returncode}): {(out or err).strip()[:300]}")
        if j.get("is_error"):
            raise LLMError(f"claude -p error: {str(j.get('result') or j)[:300]}")
        usage = j.get("usage") or {}
        model = next(iter((j.get("modelUsage") or {}).keys()), self.model or "default")
        meta = {"provider": self.name, "model": model, "cost_usd": float(j.get("total_cost_usd") or 0.0),
                "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0),
                "output_tokens": usage.get("output_tokens") or 0, "seconds": round(time.time() - t0, 1)}
        return j.get("result") or "", meta


class AnthropicAPI:
    name = "anthropic_api"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model=""):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.model = model or "claude-opus-5-5"

    def complete(self, prompt, system, cancel=None):
        body = json.dumps({"model": self.model, "max_tokens": 16000, "system": system,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(self.URL, data=body, headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"Anthropic API {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            raise LLMError(f"Anthropic API unreachable: {e}")
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage") or {}
        # Cost is not computed for the raw API path (no price table is shipped); tokens are recorded instead.
        return text, {"provider": self.name, "model": j.get("model", self.model), "cost_usd": 0.0, "cost_known": False,
                      "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                      "seconds": round(time.time() - t0, 1)}


def get_provider(settings):
    llm = (settings or {}).get("llm") or {}
    name = os.environ.get("CLIPFORGE_LLM") or llm.get("provider") or "claude_cli"
    model = llm.get("model") or ""
    if name == "anthropic_api":
        return AnthropicAPI(model)
    if name == "claude_cli":
        return ClaudeCLI(model)
    raise LLMError(f"unknown LLM provider {name!r} (use claude_cli or anthropic_api)")


def extract_json(text):
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith(("{", "[")):
        a = t.find("{")
        b = t.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("no JSON object in the reply")
        t = t[a:b + 1]
    return json.loads(t)


def complete_json(provider, prompt, system, validate, cancel=None):
    """Returns (obj, metas). validate(obj) -> list of error strings (empty = ok)."""
    metas = []
    text, meta = provider.complete(prompt, system, cancel=cancel)
    metas.append(meta)
    try:
        obj = extract_json(text)
        errs = validate(obj)
    except ValueError as e:
        obj, errs = None, [f"invalid JSON: {e}"]
    if not errs:
        return obj, metas
    repair = (prompt + "\n\n---\nYour previous reply could not be used. Problems:\n- " + "\n- ".join(errs[:20]) +
              "\n\nYour previous reply was:\n" + (text or "")[:20000] +
              "\n\nReturn the corrected JSON object only — no prose, no code fences.")
    text2, meta2 = provider.complete(repair, system, cancel=cancel)
    metas.append(meta2)
    try:
        obj2 = extract_json(text2)
        errs2 = validate(obj2)
    except ValueError as e:
        obj2, errs2 = None, [f"invalid JSON: {e}"]
    if errs2:
        err = LLMError("model output failed validation twice: " + "; ".join(errs2[:5]))
        err.metas = metas
        raise err
    return obj2, metas
