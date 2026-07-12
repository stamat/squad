"""Compression checkpoint — digests oversized context at agent boundaries,
logs the digest + before/after token counts, chunks input to fit the local
model's window. Offline (model stubbed)."""

import json
from pathlib import Path

import pytest

from codesquad import compress as comp
from codesquad.config import CompressorConfig, load_config
from codesquad.interceptor import RunLog

CONFIG = Path(__file__).parent.parent / "squad.yaml"


@pytest.fixture
def fake_llm(monkeypatch):
    """Stub the local model: 'summarizes' to a fixed short digest."""
    real = comp.litellm.completion  # capture before patch — the module attr is global

    def fake_completion(model, messages, **kw):
        return real(model=model, messages=messages, mock_response="DIGEST: the gist")
    monkeypatch.setattr(comp.litellm, "completion", fake_completion)


def records(log):
    if not log.path.exists():  # nothing logged yet
        return []
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_below_threshold_untouched(tmp_path, fake_llm):
    log = RunLog.start(tmp_path)
    cfg = CompressorConfig(trigger_tokens=1000)
    text = "short context"
    assert comp.compress(text, cfg) == text
    assert [r for r in records(log) if r["kind"] == "compress"] == []


def test_above_threshold_digested_and_logged(tmp_path, fake_llm):
    log = RunLog.start(tmp_path)
    cfg = CompressorConfig(trigger_tokens=20)
    text = "word " * 500  # way past 20 tokens
    out = comp.compress(text, cfg)
    assert out == "DIGEST: the gist"
    (rec,) = [r for r in records(log) if r["kind"] == "compress"]
    assert rec["tokens"]["in"] > rec["tokens"]["out"] > 0
    # log keeps the decision (the digest), not the flood it replaced
    assert rec["payload"]["digest"] == "DIGEST: the gist"
    assert "original" not in rec["payload"]


def test_oversized_input_chunked_to_model_window(tmp_path, monkeypatch):
    # a 50k-token string sent whole would be silently tail-truncated by the
    # local model's window; input must be chunked so every call fits
    sent = []
    real = comp.litellm.completion

    def fake_completion(model, messages, **kw):
        sent.append(messages[0]["content"])
        return real(model=model, messages=messages, mock_response="D")
    monkeypatch.setattr(comp.litellm, "completion", fake_completion)

    RunLog.start(tmp_path)
    cfg = CompressorConfig(trigger_tokens=20, window_tokens=40)
    out = comp.compress("word " * 500, cfg)
    assert len(sent) > 1                                   # chunked, not one giant call
    limit = cfg.window_tokens // 2 * 4 + len(comp._PROMPT)  # chunk + prompt overhead
    assert all(len(s) <= limit for s in sent)              # every call fits the window
    assert out == "\n".join("D" for _ in sent)             # digests joined in order


def test_delegate_compresses_oversized_context(tmp_path, fake_llm):
    from langchain_core.messages import AIMessage

    from codesquad.graph import build_delegate

    cfg = load_config(CONFIG)
    cfg.compressor.trigger_tokens = 20

    class FakeAgent:
        def invoke(self, payload, config=None):
            self.seen = payload["messages"][0]["content"]
            return {"messages": [AIMessage(content="done")]}

    log = RunLog.start(tmp_path)
    fake = FakeAgent()
    delegate = build_delegate({"coder": fake}, cfg, max_cost=1.0)
    delegate.invoke({"role": "coder", "task": "do it", "context": "word " * 500})

    assert "DIGEST: the gist" in fake.seen          # subagent got the digest…
    assert "word word word" not in fake.seen        # …not the flood
    handoff_in = next(r for r in records(log) if r["kind"] == "handoff" and r["direction"] == "in")
    assert handoff_in["payload"]["context"] == "DIGEST: the gist"
