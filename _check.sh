#!/usr/bin/env bash
# Reproduce the complete, non-mutating CI quality gate from any directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_QUALITY="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_QUALITY"' EXIT
cd "$ROOT"

export PYTHONDONTWRITEBYTECODE=1
export RUFF_CACHE_DIR="$TMPDIR_QUALITY/ruff-cache"
export COVERAGE_FILE="$TMPDIR_QUALITY/.coverage"

ruff format --check .
ruff check .
bandit -c pyproject.toml -r feeds

coverage erase
coverage run -m pytest --reuse-db -p no:cacheprovider
coverage report

python -m sphinx -W --keep-going -b html docs "$TMPDIR_QUALITY/docs"
mkdir -p "$TMPDIR_QUALITY/source"
while IFS= read -r -d '' path; do
    mkdir -p "$TMPDIR_QUALITY/source/$(dirname "$path")"
    cp -p "$path" "$TMPDIR_QUALITY/source/$path"
done < <(git ls-files -co --exclude-standard -z)
python -m build \
    --no-isolation \
    --sdist \
    --wheel \
    --outdir "$TMPDIR_QUALITY/dist" \
    "$TMPDIR_QUALITY/source"
python -m twine check "$TMPDIR_QUALITY"/dist/*
