"""Role → LiteLLM completion. The only file that knows about providers."""

import os
import time
from dataclasses import dataclass

import litellm

from codesquad.config import SquadConfig

litellm.suppress_debug_info = True


@dataclass
class PingResult:
    role: str
    model: str
    ok: bool
    latency_s: float
    cost_usd: float
    reply: str  # reply text, or error message when ok=False


def resolve_model(cfg: SquadConfig, role: str) -> str:
    """Configured model for a role. SQUAD_MODEL_OVERRIDE reroutes every role
    (e.g. ollama/qwen3:8b for keyless dev) without touching codesquad.yaml."""
    return os.environ.get("SQUAD_MODEL_OVERRIDE") or cfg.roles[role].model

def resolve_effort(cfg: SquadConfig, role: str) -> str:
    """Configured effort for a role. SQUAD_EFFORT_OVERRIDE reroutes every role
    (e.g. "low" for keyless dev) without touching codesquad.yaml."""
    return os.environ.get("SQUAD_EFFORT_OVERRIDE") or cfg.roles[role].effort

def chat_model(cfg: SquadConfig, role: str):
    """LangChain chat model for a role. Logging happens inline in the wrapper
    (squad.interceptor.LoggedChat) — litellm callbacks fire on a background
    logging worker, so records could land after the run ended; ours can't."""
    from codesquad.interceptor import LoggedChat  # lazy: heavy import

    return LoggedChat(model=resolve_model(cfg, role), squad_role=role, effort=resolve_effort(cfg, role))


def complete(cfg: SquadConfig, role: str, messages: list[dict], mock: str | None = None, **kwargs):
    """One completion for a role. mock= bypasses the network via LiteLLM's mock_response."""
    model = resolve_model(cfg, role)
    effort = resolve_effort(cfg, role)
    if mock is not None:
        kwargs["mock_response"] = mock
    return litellm.completion(model=model, messages=messages, reasoning_effort=effort, **kwargs)


def ping_role(cfg: SquadConfig, role: str, mock: bool = False) -> PingResult:
    """Smoke-test one role's model: ask it to echo the role name."""
    model = resolve_model(cfg, role)
    start = time.monotonic()
    try:
        resp = complete(
            cfg, role,
            [{"role": "user", "content": f"Reply with exactly one word: {role}"}],
            mock=role if mock else None,
        )
        cost = litellm.completion_cost(completion_response=resp) if not mock else 0.0
        return PingResult(role, model, True, time.monotonic() - start,
                          cost, resp.choices[0].message.content.strip())
    except Exception as e:  # provider errors vary wildly; ping reports, never raises
        return PingResult(role, model, False, time.monotonic() - start, 0.0, str(e))
