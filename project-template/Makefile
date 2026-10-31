.PHONY: check fix
check:   ## run the code quality gate (lint, format, size limits, tests)
	scripts/check.sh
fix:     ## let ruff fix what it can, then format
	ruff check --fix . && ruff format .
