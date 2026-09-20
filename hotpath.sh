#!/usr/bin/env sh
# Hotpath in one command (macOS / Linux / Git Bash).
#   ./hotpath.sh https://github.com/you/your-repo        # or owner/repo, or a local path
#   ./hotpath.sh https://github.com/you/your-repo --yes  # accept every default, including pushing the PR branch
# Creates .venv next to this script on first use, installs Hotpath into it, then runs `hotpath go`.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "usage: ./hotpath.sh <github-url | owner/repo | git-url | local-path> [options]"
  echo "       ./hotpath.sh --help-go    (all options)"
  [ "$#" -eq 0 ] && exit 2 || exit 0
fi

# Resolve a local-path target before changing directory, so relative paths keep working.
first="$1"; shift
if [ "$first" = "--help-go" ]; then first="--help"; fi
if [ -d "$first" ]; then first="$(cd "$first" && pwd)"; fi

find_python() {
  for c in python3.13 python3.12 python3.11 python3.14 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

VENV="$HERE/.venv"
if [ -x "$VENV/bin/python" ]; then PY="$VENV/bin/python"
elif [ -x "$VENV/Scripts/python.exe" ]; then PY="$VENV/Scripts/python.exe"
else
  BASE="$(find_python)" || { echo "Hotpath needs Python 3.11 or newer: https://www.python.org/downloads/"; exit 1; }
  echo "[setup] creating $VENV"
  "$BASE" -m venv "$VENV"
  if [ -x "$VENV/bin/python" ]; then PY="$VENV/bin/python"; else PY="$VENV/Scripts/python.exe"; fi
fi

STAMP="$VENV/.hotpath-installed"
if [ ! -f "$STAMP" ] || [ "$HERE/pyproject.toml" -nt "$STAMP" ]; then
  echo "[setup] installing Hotpath into $VENV (first run only)"
  "$PY" -m pip install --disable-pip-version-check -q -e "$HERE"
  date > "$STAMP"
fi

command -v git >/dev/null 2>&1 || { echo "git is not installed: https://git-scm.com/downloads"; exit 1; }

cd "$HERE"
PYTHONUTF8=1 exec "$PY" -m hotpath.cli go "$first" "$@"
