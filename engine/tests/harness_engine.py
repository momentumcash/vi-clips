#!/usr/bin/env python3
"""TEST-ONLY: run the real engine server with the LLM swapped for tests/fake_llm.FakeProvider.
Usage: harness_engine.py <port> <data_dir>"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["CLIPFORGE_DATA"] = sys.argv[2]

import llm  # noqa: E402
from fake_llm import FakeProvider  # noqa: E402

_fake = FakeProvider()
llm.get_provider = lambda settings: _fake

import server  # noqa: E402

server.serve("127.0.0.1", int(sys.argv[1]), sys.argv[2])
