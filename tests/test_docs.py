"""save_doc — run documents land in logs/<run-id>/, traversal-safe, logged."""

import json

import pytest

from squad.interceptor import RunLog, current_log
from squad.tools.docs import save_doc


def test_save_doc_writes_markdown_next_to_run_log(tmp_path):
    log = RunLog.start(tmp_path)
    out = save_doc.invoke({"name": "report", "content": "# Findings\nstuff"})
    path = tmp_path / log.run_id / "report.md"
    assert path.read_text() == "# Findings\nstuff"
    assert str(path) in out
    recs = [json.loads(l) for l in log.path.read_text().splitlines()]
    (rec,) = [r for r in recs if r["kind"] == "doc"]
    assert rec["payload"]["name"] == "report.md"


def test_save_doc_name_is_traversal_safe(tmp_path):
    log = RunLog.start(tmp_path)
    save_doc.invoke({"name": "../../evil.md", "content": "x"})
    assert (tmp_path / log.run_id / "evil.md").exists()   # confined to the docs dir
    assert not (tmp_path.parent.parent / "evil.md").exists()


def test_save_doc_requires_active_run():
    current_log.set(None)
    with pytest.raises(RuntimeError, match="no active run"):
        save_doc.invoke({"name": "report", "content": "x"})
