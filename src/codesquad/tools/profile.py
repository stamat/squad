"""Linguist-style repo profile: languages by byte share + test/lint pipeline,
one deterministic call. Saves the scout N model turns of ls/glob/read per
discovery — the profile is computed, not reasoned."""

from pathlib import Path

from langchain_core.tools import tool

_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".cs": "C#", ".sh": "Shell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
    ".svelte": "Svelte", ".lua": "Lua", ".ex": "Elixir", ".exs": "Elixir", ".zig": "Zig",
}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
              "build", "target", ".next", "vendor", ".tox", ".mypy_cache"}
# tooling markers: filename (or filename:needle) → what it tells us
_TOOLING = {
    "pyproject.toml": "Python project (uv/pip)", "package.json": "Node project",
    "go.mod": "Go module", "Cargo.toml": "Rust crate", "Gemfile": "Ruby bundler",
    "composer.json": "PHP composer", "pytest.ini": "pytest", "tox.ini": "tox",
    "ruff.toml": "ruff", ".eslintrc.json": "eslint", ".eslintrc.js": "eslint",
    "eslint.config.js": "eslint", ".prettierrc": "prettier", "jest.config.js": "jest",
    "vitest.config.ts": "vitest", "Makefile": "make", ".github/workflows": "GitHub CI",
    "phpunit.xml": "phpunit", ".rubocop.yml": "rubocop", "setup.cfg": "Python setup.cfg",
}
_NEEDLES = {  # things declared inside manifests, not as their own file
    "pyproject.toml": {"[tool.pytest": "pytest", "[tool.ruff": "ruff",
                       "[tool.mypy": "mypy", "[tool.black": "black"},
    "package.json": {'"jest"': "jest", '"vitest"': "vitest", '"eslint"': "eslint",
                     '"mocha"': "mocha", '"prettier"': "prettier"},
}


def profile_repo(root: Path) -> str:
    """Compact markdown profile: language shares by bytes + detected tooling."""
    bytes_per_lang: dict[str, int] = {}
    for p in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and (lang := _LANG.get(p.suffix)):
            bytes_per_lang[lang] = bytes_per_lang.get(lang, 0) + p.stat().st_size

    tooling = [label for name, label in _TOOLING.items() if (root / name).exists()]
    for manifest, needles in _NEEDLES.items():
        f = root / manifest
        if f.is_file():
            text = f.read_text(errors="replace")
            tooling += [label for needle, label in needles.items() if needle in text]

    total = sum(bytes_per_lang.values())
    if not total:
        return "no recognized source files found"
    langs = sorted(bytes_per_lang.items(), key=lambda kv: -kv[1])
    lang_lines = "\n".join(f"- {name}: {100 * b // total}%" for name, b in langs)
    tool_lines = "\n".join(f"- {t}" for t in dict.fromkeys(tooling)) or "- none detected"
    return f"## Languages (by bytes)\n{lang_lines}\n\n## Tooling\n{tool_lines}"


def make_profile(jail: Path):
    @tool
    def profile() -> str:
        """Language profile (dominant + minor, by byte share) and test/lint
        tooling of the repo — one deterministic call. Use this FIRST in
        discovery instead of exploring files by hand."""
        return profile_repo(jail)

    return profile
