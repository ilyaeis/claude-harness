#!/usr/bin/env bash
# The code quality gate. Run it before every commit. The commit hook runs it too.
# Steps: ruff lint, ruff format check, size limits (tools/loc_check.py), tests.
# Set PYTHON to use another interpreter (default: .venv/bin/python if it exists, else python3).
set -uo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
RUFF="${RUFF:-$(command -v ruff || echo "$PYTHON -m ruff")}"

failed=()
step() {
  local name="$1"; shift
  echo "== $name"
  "$@" || failed+=("$name")
}
step "ruff check" $RUFF check .
step "ruff format --check" $RUFF format --check .
step "loc_check" "$PYTHON" tools/loc_check.py
step "pytest" "$PYTHON" -m pytest -q

if [ ${#failed[@]} -gt 0 ]; then
  echo "GATE FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "GATE PASSED"
