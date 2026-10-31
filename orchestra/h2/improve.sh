#!/usr/bin/env bash
# Harness2 improve loop (Propose -> Probe -> Compose) for one orchestra skill. See improve.py for usage.
exec python3 "$(dirname "$(readlink -f "$0")")/improve.py" "$@"
