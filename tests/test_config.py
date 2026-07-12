"""Config validation tests — every failure mode must fail loud."""

from pathlib import Path

import pytest
import yaml

from squad.config import GitConfig, SquadConfig, load_config

REPO_ROOT = Path(__file__).parent.parent


def minimal(**overrides) -> dict:
    """Smallest valid config dict; override pieces per test."""
    cfg = {
        "roles": {
            "supervisor": {"model": "gemini/gemini-3-flash", "prompt": "prompts/supervisor.md"},
        }
    }
    cfg.update(overrides)
    return cfg


def write_config(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "prompts").mkdir(exist_ok=True)
    for role in data.get("roles", {}).values():
        p = tmp_path / role["prompt"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# prompt stub")
    path = tmp_path / "squad.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# --- happy paths ---


def test_repo_default_config_loads():
    cfg = load_config(REPO_ROOT / "squad.yaml")
    assert set(cfg.roles) == {"supervisor", "planner", "scout", "scribe", "coder", "reviewer"}
    for role in cfg.roles.values():
        assert role.prompt.exists()


def test_minimal_config_loads(tmp_path):
    cfg = load_config(write_config(tmp_path, minimal()))
    assert "supervisor" in cfg.roles
    assert cfg.git.push == "confirm"  # default: push always gated


def test_prompt_paths_resolve_relative_to_config_file(tmp_path):
    cfg = load_config(write_config(tmp_path, minimal()))
    assert cfg.roles["supervisor"].prompt == (tmp_path / "prompts/supervisor.md").resolve()


def test_worktrees_dir_expands_home():
    assert "~" not in str(GitConfig(worktrees_dir="~/.squad/worktrees").worktrees_dir)


def test_mcp_server_names_are_bindable_tools(tmp_path):
    data = minimal(mcp_servers={"playwright": {"command": "npx"}})
    data["roles"]["scout"] = {
        "model": "openai/gpt-5-mini",
        "prompt": "prompts/scout.md",
        "tools": ["playwright"],
    }
    cfg = load_config(write_config(tmp_path, data))
    assert cfg.roles["scout"].tools == ["playwright"]


# --- failure modes ---


def test_missing_config_file():
    with pytest.raises(FileNotFoundError, match="config not found"):
        load_config(Path("/nonexistent/squad.yaml"))


def test_missing_supervisor_role(tmp_path):
    data = {"roles": {"coder": {"model": "x", "prompt": "prompts/coder.md"}}}
    with pytest.raises(ValueError, match="supervisor"):
        load_config(write_config(tmp_path, data))


def test_unknown_tool_rejected(tmp_path):
    data = minimal()
    data["roles"]["supervisor"]["tools"] = ["warp_drive"]
    with pytest.raises(ValueError, match="warp_drive"):
        load_config(write_config(tmp_path, data))


def test_commit_role_must_exist(tmp_path):
    data = minimal(git={"commit_roles": ["ghost"]})
    with pytest.raises(ValueError, match="ghost"):
        load_config(write_config(tmp_path, data))


def test_invalid_regex_rejected(tmp_path):
    data = minimal(shell_rules={"deny_patterns": ["([unclosed"]})
    with pytest.raises(ValueError, match="invalid regex"):
        load_config(write_config(tmp_path, data))


def test_missing_prompt_file(tmp_path):
    path = write_config(tmp_path, minimal())
    (tmp_path / "prompts/supervisor.md").unlink()
    with pytest.raises(FileNotFoundError, match="supervisor"):
        load_config(path)


def test_push_cannot_be_disabled():
    """push: confirm is the only allowed value — no yolo mode."""
    with pytest.raises(ValueError):
        SquadConfig.model_validate(
            {
                "roles": {"supervisor": {"model": "x", "prompt": "p.md"}},
                "git": {"push": "auto"},
            }
        )
