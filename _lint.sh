#!/usr/bin/env bash
# Apply the same Ruff policy enforced by _check.sh. Security checks stay
# check-only because Bandit findings require human review.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ruff check --fix .
ruff format .
bandit -c pyproject.toml -r feeds
