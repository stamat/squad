"""Gated local shell executor: deny → confirm → cwd jail → timeout → truncate.

Returns agent-visible strings, never raises — the agent must always learn
what happened (and why a command was refused).
"""

import subprocess
from pathlib import Path
from typing import Callable

from squad.config import ShellRules
from squad.interceptor import current_log
from squad.rules import check_command


def run_shell(cmd: str, rules: ShellRules, jail: Path, confirm: Callable[[str], bool]) -> str:
    result = _execute(cmd, rules, jail, confirm)
    if log := current_log.get():
        verdict, _ = check_command(cmd, rules)
        log.write("shell", payload={"cmd": cmd, "verdict": verdict, "result": result[:1000]})
    return result


def _execute(cmd: str, rules: ShellRules, jail: Path, confirm: Callable[[str], bool]) -> str:
    verdict, pattern = check_command(cmd, rules)
    if verdict == "deny":
        return f"DENIED: command matches blocked pattern {pattern!r}. Not executed."
    if verdict == "confirm" and not confirm(cmd):
        return "DECLINED: human refused this command. Not executed."
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=jail, timeout=rules.timeout_seconds,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return f"TIMED OUT after {rules.timeout_seconds}s. Not completed."
    out = (proc.stdout + proc.stderr)
    if len(out) > rules.max_output_bytes:
        # cut the middle: head keeps the banner/first error, tail keeps the final
        # error/summary — the part that usually matters. A single head-cut fed
        # the agent 100KB of noise and dropped the conclusion.
        head, tail = (rules.max_output_bytes * 2) // 3, rules.max_output_bytes // 3
        out = out[:head] + "\n[... output truncated ...]\n" + out[-tail:]
    return f"exit {proc.returncode}\n{out}"
