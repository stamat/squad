"""Agent construction — capability boundary: a tool not in the role's list is not bound."""

from pathlib import Path

import pytest

from codesquad.agents import build_agent, fs_permissions, load_prompt
from codesquad.config import load_config

CONFIG = Path(__file__).parent.parent / "squad.yaml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


def bound_tools(agent) -> set[str]:
    """Names of tools actually bound into the compiled agent graph."""
    return {t.name for t in agent.nodes["tools"].bound._tools_by_name.values()}


def test_coder_gets_shell(cfg, tmp_path):
    agent = build_agent(cfg, "coder", tmp_path, lambda c: False)
    assert "shell" in bound_tools(agent)


@pytest.mark.parametrize("role", ["scout", "scribe", "planner", "reviewer", "supervisor"])
def test_only_coder_gets_shell(cfg, tmp_path, role):
    # THE security property: no shell binding for browsing/reading roles.
    agent = build_agent(cfg, role, tmp_path, lambda c: False)
    assert "shell" not in bound_tools(agent)


def test_scout_gets_fetch_others_do_not(cfg, tmp_path):
    assert "fetch" in bound_tools(build_agent(cfg, "scout", tmp_path, lambda c: False))
    assert "fetch" not in bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False))


def test_subtask_tools_split_between_planner_and_coder(cfg, tmp_path):
    planner = bound_tools(build_agent(cfg, "planner", tmp_path, lambda c: False))
    coder = bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False))
    assert "set_subtasks" in planner and "set_subtasks" not in coder  # planner pushes
    assert {"next_subtask", "complete_subtask"} <= coder               # coder pulls
    assert "next_subtask" not in planner


def test_coder_and_reviewer_get_principles_others_do_not(cfg):
    # the law is injected where {principles} appears, verbatim into others
    law = (CONFIG.parent / "prompts" / "principles.md").read_text().strip()
    for role in ("coder", "reviewer"):
        prompt = load_prompt(cfg.roles[role].prompt)
        assert law in prompt and "{principles}" not in prompt
    for role in ("scout", "supervisor"):
        assert law not in load_prompt(cfg.roles[role].prompt)


def test_scout_gets_save_doc_others_do_not(cfg, tmp_path):
    assert "save_doc" in bound_tools(build_agent(cfg, "scout", tmp_path, lambda c: False))
    assert "save_doc" not in bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False))


def test_scout_gets_profile_others_do_not(cfg, tmp_path):
    assert "profile" in bound_tools(build_agent(cfg, "scout", tmp_path, lambda c: False))
    assert "profile" not in bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False))


def test_history_middleware_wires_max_context_and_keep(cfg):
    # the dead knobs live: max_context triggers history summarization by the
    # local compressor model; keep_last_messages is the untouched tail
    from codesquad.agents import history_middleware

    (mw,) = history_middleware(cfg, "coder")
    assert mw.trigger == ("tokens", cfg.roles["coder"].max_context)
    assert mw.keep == ("messages", cfg.compressor.keep_last_messages)
    assert mw.model.model == cfg.compressor.model         # local, free
    assert mw.model.squad_role == "compressor"            # spend attributed right


def test_fs_read_roles_get_write_deny_permission():
    # read-only is enforced, not asked: fs_read without fs denies every write path
    (rule,) = fs_permissions(["fs_read"])
    assert rule.mode == "deny" and rule.operations == ["write"] and rule.paths == ["/**"]
    assert fs_permissions(["fs", "fs_read"]) is None   # writer roles unrestricted
    assert fs_permissions(["fs"]) is None
    assert fs_permissions([]) is None


def test_git_commit_bound_only_with_run_id_and_commit_role(cfg, tmp_path):
    # inside a worktree run: coder commits, reviewer never
    assert "git_commit" in bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False, run_id="r1"))
    assert "git_commit" not in bound_tools(build_agent(cfg, "reviewer", tmp_path, lambda c: False, run_id="r1"))
    # outside a worktree (no run_id): nobody commits
    assert "git_commit" not in bound_tools(build_agent(cfg, "coder", tmp_path, lambda c: False))
