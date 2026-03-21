#!/usr/bin/env bash
# Build sdist + wheel and upload to PyPI. Run from anywhere; always uses this repo root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

rm -rf dist build
for d in *.egg-info; do
  [[ -d "$d" ]] && rm -rf "$d"
done
mkdir -p dist

python -m pip install --upgrade pip setuptools wheel twine

# pkg_resources is part of setuptools; without it, egg_info can crash (e.g. if pbr is installed).
if ! python -c "import pkg_resources" 2>/dev/null; then
  echo "pkg_resources missing; force-reinstalling setuptools..." >&2
  python -m pip install --force-reinstall "setuptools>=69"
fi
if ! python -c "import pkg_resources" 2>/dev/null; then
  echo "error: still no pkg_resources after reinstalling setuptools." >&2
  echo "  Try:  python -m pip uninstall -y pbr   # if you don't need it" >&2
  echo "  And:  which python && python -m pip --version   # same interpreter for pip + build" >&2
  exit 1
fi

python setup.py sdist bdist_wheel

shopt -s nullglob
artifacts=(dist/*.tar.gz dist/*.whl)
shopt -u nullglob

if (( ${#artifacts[@]} == 0 )); then
  echo "error: no sdist/wheel in dist/; build produced nothing" >&2
  exit 1
fi

twine upload "${artifacts[@]}"

rm -rf build dist
