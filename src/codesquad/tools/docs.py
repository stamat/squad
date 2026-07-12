"""Run documents: the logs/ directory is the job database. Scout saves its
discovery report, code style note and PR notes as markdown next to the run log —
logs/<run-id>/<name>.md — where the CLI and the human can find them."""

from pathlib import Path

from langchain_core.tools import tool

from codesquad.interceptor import current_log


def docs_dir(log_path: Path, run_id: str) -> Path:
    """logs/<run-id>/ — sibling of the run's JSONL."""
    return log_path.parent / run_id


@tool
def save_doc(name: str, content: str) -> str:
    """Save a run document (discovery report, code style note, PR notes) as
    markdown in the run's docs directory. `name` is a bare filename like
    'report' or 'pr-notes'; '.md' is added for you."""
    log = current_log.get()
    if log is None:  # tools only run inside a squad run
        raise RuntimeError("no active run — save_doc needs a RunLog")
    safe = Path(name).name.removesuffix(".md")  # filename only; no path traversal
    d = docs_dir(log.path, log.run_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{safe}.md"
    path.write_text(content)
    log.write("doc", payload={"name": path.name, "chars": len(content)})
    return f"saved {path}"
