#!/usr/bin/env python3
"""Download faster-whisper models into the Hugging Face cache (~/.cache/huggingface/hub).

The engine never downloads models on its own during a job, so run this once after install:
    .venv/bin/python engine/tools/get_models.py              # small (needed; ~0.5 GB)
    .venv/bin/python engine/tools/get_models.py small large-v3-turbo   # + better model for sources <= 30 min (~1.6 GB)
"""
import sys

from faster_whisper import WhisperModel


def main(names):
    for name in names or ["small"]:
        print(f"downloading {name} ...", flush=True)
        WhisperModel(name, device="cpu", compute_type="int8")
        print(f"ok {name}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
