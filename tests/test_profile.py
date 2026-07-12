"""Repo profile — languages by byte share, tooling detection, junk dirs skipped."""

from squad.tools.profile import profile_repo


def test_dominant_and_minor_languages_by_bytes(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n" * 300)
    (tmp_path / "util.py").write_text("y = 2\n" * 300)
    (tmp_path / "page.js").write_text("let z;\n" * 10)
    out = profile_repo(tmp_path)
    py, js = out.index("Python"), out.index("JavaScript")
    assert py < js                      # dominant first
    assert "%" in out


def test_junk_dirs_ignored(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    dep = tmp_path / "node_modules" / "lib"
    dep.mkdir(parents=True)
    (dep / "huge.js").write_text("z" * 100_000)
    out = profile_repo(tmp_path)
    assert "JavaScript" not in out      # vendored bytes don't skew the profile


def test_tooling_from_files_and_manifest_needles(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
    out = profile_repo(tmp_path)
    assert "pytest" in out and "ruff" in out
    assert "Python project" in out


def test_empty_repo_says_so(tmp_path):
    assert "no recognized source files" in profile_repo(tmp_path)
