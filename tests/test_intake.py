"""Task intake router — gh:/linear:/plain, slugs, issue comment. Offline (gh stubbed)."""

import json
import subprocess

import pytest

from squad import intake


def gh_stub(monkeypatch, stdout="", returncode=0, stderr=""):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(intake.subprocess, "run", fake_run)
    return calls


def test_plain_prompt_passes_through(tmp_path):
    t = intake.resolve_task("Fix the login bug in auth.py", tmp_path)
    assert t.text == "Fix the login bug in auth.py"
    assert t.slug == "fix-the-login-bug-in-auth-py"
    assert t.gh_issue is None


def test_slug_is_short_and_safe(tmp_path):
    t = intake.resolve_task("Ünïcode!!! & very " + "long " * 30 + "prompt", tmp_path)
    assert len(t.slug) <= 30
    assert t.slug and all(c.isalnum() or c == "-" for c in t.slug)


def test_gh_issue_fetched_with_exact_fields(tmp_path, monkeypatch):
    payload = json.dumps({"title": "Login broken", "body": "500 on POST /login",
                          "labels": [{"name": "bug"}]})
    calls = gh_stub(monkeypatch, stdout=payload)
    t = intake.resolve_task("gh:123", tmp_path)
    assert calls[0][:4] == ["gh", "issue", "view", "123"]
    assert "--json" in calls[0]                      # exact fields, no token waste
    assert "Login broken" in t.text and "500 on POST /login" in t.text
    assert "bug" in t.text
    assert t.slug == "gh-123" and t.gh_issue == 123


def test_gh_failure_is_loud(tmp_path, monkeypatch):
    gh_stub(monkeypatch, returncode=1, stderr="no auth")
    with pytest.raises(RuntimeError, match="gh issue view 7 failed"):
        intake.resolve_task("gh:7", tmp_path)


def test_linear_issue_tagged_for_mcp(tmp_path):
    t = intake.resolve_task("linear:ABC-42", tmp_path)
    assert "ABC-42" in t.text and "linear" in t.text.lower()
    assert t.slug == "abc-42" and t.gh_issue is None


def test_comment_on_issue_best_effort(tmp_path, monkeypatch):
    calls = gh_stub(monkeypatch)
    assert "commented" in intake.comment_on_issue(5, "report", tmp_path)
    assert calls[0][:4] == ["gh", "issue", "comment", "5"]
    gh_stub(monkeypatch, returncode=1, stderr="boom")
    assert "failed" in intake.comment_on_issue(5, "report", tmp_path)  # never raises
