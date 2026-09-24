#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ ! -d .venv ]]; then
    python_command=()
    for candidate in python3 python py; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        candidate_command=("$candidate")
        if [[ "$candidate" == py ]]; then
            candidate_command+=(-3)
        fi
        if "${candidate_command[@]}" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
            python_command=("${candidate_command[@]}")
            break
        fi
    done

    if [[ ${#python_command[@]} -eq 0 ]]; then
        echo "Python 3.10 or newer is required. Install Python, then run this script again." >&2
        exit 1
    fi

    echo "Creating the virtual environment..."
    "${python_command[@]}" -m venv .venv
fi

if [[ -x .venv/bin/python ]]; then
    venv_python=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
    venv_python=.venv/Scripts/python.exe
else
    echo "The .venv folder has no usable Python. Rename it, then run setup.sh again." >&2
    exit 1
fi

if ! "$venv_python" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "The virtual environment requires Python 3.10 or newer. Rename .venv and run setup.sh again." >&2
    exit 1
fi

echo "Installing Python requirements..."
"$venv_python" -m pip install -r requirements.txt

echo "Installing Chromium..."
"$venv_python" -m playwright install chromium

echo "Setup complete. Start the downloader with: bash run.sh"
