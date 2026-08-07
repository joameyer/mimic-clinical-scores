#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"
python3 -m venv "$PROJECT_ROOT/.venv"
"$PROJECT_ROOT/.venv/bin/python" -m pip install -r "$PROJECT_ROOT/requirements.lock"
"$PROJECT_ROOT/.venv/bin/python" -m pip install --no-deps -e "$PROJECT_ROOT"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/bootstrap_mimic_code.py" --project-root "$PROJECT_ROOT"

