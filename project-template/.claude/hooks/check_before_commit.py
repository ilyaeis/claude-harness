"""Claude Code PreToolUse hook: run the code quality gate before an agent commits or opens
a pull request, and block the call when the gate fails.

It reads the Bash tool call as JSON on stdin. A command with ``git commit`` or
``gh pr create`` triggers it. ``--no-verify`` (and ``git commit -n``) is always blocked.
Exit code 2 blocks the call and shows stderr to the agent. Exit code 0 lets it run.
The gate is ``scripts/check.sh``: ruff check, ruff format --check, tools/loc_check.py, pytest.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

COMMIT_PATTERN = re.compile(r"\bgit(?:\s+-[Cc]\s+\S+)*\s+commit\b")
"""``git commit``, also with ``-C <dir>`` or ``-c <key=value>`` before it.
A path such as ``.pre-commit-config.yaml`` later in the command is no commit."""
PR_PATTERN = re.compile(r"\bgh\s+pr\s+create\b")
NO_VERIFY_PATTERN = re.compile(r"--no-verify\b|\bgit(?:\s+-[Cc]\s+\S+)*\s+commit\b[^;&|]*\s-n\b")
GATE = Path("scripts/check.sh")
GATE_TIMEOUT_S = 170
"""Stay under the hook timeout in .claude/settings.json (180 s)."""
OUTPUT_TAIL_CHARS = 4000
"""Keep the agent's context small: show only the end of a long gate output."""


def main() -> int:
    command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    if not (COMMIT_PATTERN.search(command) or PR_PATTERN.search(command)):
        return 0
    if NO_VERIFY_PATTERN.search(command):
        print("Blocked: do not skip the commit checks (--no-verify / -n).", file=sys.stderr)
        return 2
    if not GATE.exists():
        return 0
    failure = run_gate()
    if failure:
        print("Blocked: the quality gate failed. Fix these, then commit again.\n", file=sys.stderr)
        print(failure, file=sys.stderr)
        return 2
    return 0


def run_gate() -> str:
    """The gate output when it fails, or an empty string when it passes."""
    try:
        result = subprocess.run([str(GATE)], capture_output=True, text=True, timeout=GATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"{GATE} took more than {GATE_TIMEOUT_S} s."
    if result.returncode == 0:
        return ""
    return (result.stdout + result.stderr).strip()[-OUTPUT_TAIL_CHARS:]


if __name__ == "__main__":
    raise SystemExit(main())
