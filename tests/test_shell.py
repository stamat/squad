"""Gated shell executor — jail, confirm flow, timeout, truncation. No network, no model."""

from codesquad.config import ShellRules
from codesquad.tools.shell import run_shell

RULES = ShellRules(
    confirm_patterns=[r"\bsudo\b"],
    deny_patterns=[r"git\s+worktree\s+remove"],
    timeout_seconds=2,
    max_output_bytes=200,
)

YES = lambda cmd: True
NO = lambda cmd: False


def test_runs_and_returns_output(tmp_path):
    out = run_shell("echo hello", RULES, tmp_path, YES)
    assert "hello" in out and "exit 0" in out


def test_cwd_is_jailed(tmp_path):
    assert str(tmp_path) in run_shell("pwd", RULES, tmp_path, YES)


def test_deny_blocks_whole_command(tmp_path):
    out = run_shell("touch pwned; git worktree remove x", RULES, tmp_path, NO)
    assert "denied" in out.lower()
    assert not (tmp_path / "pwned").exists()  # nothing executed


def test_confirm_declined_blocks(tmp_path):
    out = run_shell("sudo touch pwned", RULES, tmp_path, NO)
    assert "declined" in out.lower()
    assert not (tmp_path / "pwned").exists()


def test_confirm_accepted_runs(tmp_path):
    # 'sudo' pattern trips confirm; use a command that works without root anyway
    asked = []
    out = run_shell("echo sudoku", RULES, tmp_path, lambda c: asked.append(c) or True)
    assert asked == []  # benign command never asks
    out = run_shell("echo run && true # sudo", RULES, tmp_path, lambda c: asked.append(c) or True)
    assert asked and "exit 0" in out


def test_timeout(tmp_path):
    out = run_shell("sleep 10", RULES, tmp_path, YES)
    assert "timed out" in out.lower()


def test_output_truncated_keeps_head_and_tail(tmp_path):
    # long output cut in the middle: the start (command banner/first error) and
    # the end (final error/summary — usually the part that matters) both survive
    out = run_shell("printf 'START'; yes x | head -c 10000; printf 'THEEND'", RULES, tmp_path, YES)
    assert len(out) < 1000 and "truncated" in out.lower()
    assert "START" in out and "THEEND" in out


def test_nonzero_exit_reported(tmp_path):
    assert "exit 3" in run_shell("exit 3", RULES, tmp_path, YES)
