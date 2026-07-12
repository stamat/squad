"""Router tests — all offline via LiteLLM mock_response. No keys needed."""

from pathlib import Path

import pytest

from codesquad.config import load_config
from codesquad.router import complete, ping_role, resolve_model

CONFIG = Path(__file__).parent.parent / "squad.yaml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


def test_complete_routes_role_to_configured_model(cfg):
    resp = complete(cfg, "supervisor", [{"role": "user", "content": "hi"}], mock="pong")
    assert resp.choices[0].message.content == "pong"
    assert resp.model.endswith(cfg.roles["supervisor"].model.split("/")[-1])


def test_ping_all_roles_mock(cfg):
    for role in cfg.roles:
        r = ping_role(cfg, role, mock=True)
        assert r.ok, f"{role}: {r.reply}"
        assert r.reply == role
        assert r.cost_usd == 0.0


def test_model_override_reroutes_every_role(cfg, monkeypatch):
    monkeypatch.setenv("SQUAD_MODEL_OVERRIDE", "ollama/gemma3n")
    assert all(resolve_model(cfg, r) == "ollama/gemma3n" for r in cfg.roles)
    monkeypatch.delenv("SQUAD_MODEL_OVERRIDE")
    assert resolve_model(cfg, "planner") == "anthropic/claude-opus-4-8"


def test_ping_reports_failure_instead_of_raising(cfg):
    cfg_bad = cfg.model_copy(deep=True)
    cfg_bad.roles["supervisor"].model = "nonexistent/not-a-model"
    r = ping_role(cfg_bad, "supervisor")
    assert not r.ok
    assert r.reply  # error message surfaced
