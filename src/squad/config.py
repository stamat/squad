"""Load and validate squad.yaml. Fail loud on bad config."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

BUILTIN_TOOLS = {"shell", "fs", "fs_read", "browse", "render", "git_commit"}


class RoleConfig(BaseModel):
    model: str  # LiteLLM model string, e.g. "anthropic/claude-opus-4-8"
    prompt: Path
    tools: list[str] = Field(default_factory=list)
    max_context: int = 100_000
    max_turns: int = 20


class CompressorConfig(BaseModel):
    model: str = "ollama/qwen3:8b"
    trigger_tokens: int = 50_000
    keep_last_messages: int = 6


class GitConfig(BaseModel):
    worktrees_dir: Path = Path("~/.squad/worktrees")
    branch_prefix: str = "squad/"
    commit_roles: list[str] = Field(default_factory=lambda: ["coder"])
    push: Literal["confirm"] = "confirm"  # push always requires a human yes
    # run end: confirm = ask before push+PR; auto = push+PR unattended; never = branch stays local
    pr: Literal["confirm", "never", "auto"] = "confirm"

    @field_validator("worktrees_dir")
    @classmethod
    def expand(cls, v: Path) -> Path:
        return v.expanduser()


class ShellRules(BaseModel):
    confirm_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    timeout_seconds: int = 120
    max_output_bytes: int = 100_000

    @field_validator("confirm_patterns", "deny_patterns")
    @classmethod
    def patterns_compile(cls, v: list[str]) -> list[str]:
        for p in v:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"invalid regex {p!r}: {e}") from e
        return v


class SquadConfig(BaseModel):
    roles: dict[str, RoleConfig]
    compressor: CompressorConfig = CompressorConfig()
    git: GitConfig = GitConfig()
    shell_rules: ShellRules = ShellRules()
    mcp_servers: dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_cross_references(self) -> "SquadConfig":
        if "supervisor" not in self.roles:
            raise ValueError("config must define a 'supervisor' role")
        known_tools = BUILTIN_TOOLS | set(self.mcp_servers)
        for name, role in self.roles.items():
            unknown = set(role.tools) - known_tools
            if unknown:
                raise ValueError(
                    f"role {name!r} references unknown tools {sorted(unknown)}; "
                    f"known: {sorted(known_tools)}"
                )
        missing = set(self.git.commit_roles) - set(self.roles)
        if missing:
            if "commit_roles" in self.git.model_fields_set:
                raise ValueError(f"git.commit_roles references undefined roles {sorted(missing)}")
            # default commit_roles: keep only roles that actually exist
            self.git.commit_roles = [r for r in self.git.commit_roles if r in self.roles]
        return self


def load_config(path: Path) -> SquadConfig:
    """Parse and validate squad.yaml. Prompt paths are resolved relative to the config file."""
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    cfg = SquadConfig.model_validate(data)
    base = path.parent
    for name, role in cfg.roles.items():
        role.prompt = (base / role.prompt).resolve()
        if not role.prompt.exists():
            raise FileNotFoundError(f"prompt file for role {name!r} not found: {role.prompt}")
    return cfg
