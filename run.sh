#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ -x .venv/bin/python ]]; then
    venv_python=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
    venv_python=.venv/Scripts/python.exe
else
    echo "Setup is required first. Run: bash setup.sh" >&2
    exit 1
fi

if ! "$venv_python" -c 'import playwright.async_api' >/dev/null 2>&1; then
    echo "Requirements are missing. Run: bash setup.sh" >&2
    exit 1
fi

exec "$venv_python" tronclass_downloader.py "$@"
