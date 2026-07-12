"""Shell command gate: deny → confirm → allow. Driven by squad.yaml shell_rules."""

import re
from typing import Literal

from codesquad.config import ShellRules

Verdict = Literal["allow", "confirm", "deny"]


def check_command(cmd: str, rules: ShellRules) -> tuple[Verdict, str | None]:
    """Return (verdict, matched pattern). Deny is checked first — it always wins."""
    for pattern in rules.deny_patterns:
        if re.search(pattern, cmd):
            return "deny", pattern
    for pattern in rules.confirm_patterns:
        if re.search(pattern, cmd):
            return "confirm", pattern
    return "allow", None
