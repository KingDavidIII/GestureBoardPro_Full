#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "==> GestureBoardPro Codespace bootstrap"
echo "Repository: ${ROOT_DIR}"

cd "${ROOT_DIR}"

echo "==> Installing Linux runtime dependencies"

# GestureBoardPro uses npm, not the legacy Yarn APT repository.
# Disable only active Yarn source files; ignore already-disabled files.
for source_file in \
    /etc/apt/sources.list.d/*.list \
    /etc/apt/sources.list.d/*.sources
do
    [[ -f "${source_file}" ]] || continue

    if sudo grep -q "dl.yarnpkg.com" "${source_file}"; then
        echo "==> Disabling incompatible Yarn APT source: ${source_file}"
        sudo mv "${source_file}" "${source_file}.disabled"
    fi
done

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0

echo "==> Preparing Python 3.11 environment"

PYTHON_BIN="$(command -v python)"

if [[ -z "${PYTHON_BIN}" ]]; then
    echo "ERROR: Python is not available in the container."
    exit 1
fi

"${PYTHON_BIN}" - <<'PYTHON_CHECK'
import sys

if sys.version_info[:2] != (3, 11):
    raise SystemExit(
        f"ERROR: GestureBoardPro requires Python 3.11; "
        f"found {sys.version.split()[0]}"
    )

print(f"Using Python {sys.version.split()[0]}")
PYTHON_CHECK

# A failed venv creation may leave a partial directory behind.
if [[ -d "${VENV_DIR}" && ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "==> Removing incomplete virtual environment"
    rm -rf "${VENV_DIR}"
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    # --copies avoids symlink problems that can occur with the
    # /usr/local Python layout used by some Dev Container images.
    "${PYTHON_BIN}" -m venv --copies "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m ensurepip --upgrade
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -r backend/requirements.txt

echo "==> Preparing environment configuration"

if [[ ! -f .env ]]; then
    cp .env.example .env
fi

echo "==> Installing frontend dependencies"

cd frontend
npm ci
cd "${ROOT_DIR}"

echo "==> Verifying native Python dependencies"

"${VENV_DIR}/bin/python" - <<'PY'
import sys

import cv2
import django
import mediapipe as mp

assert sys.version_info[:2] == (3, 11), sys.version

print(f"Python:     {sys.version.split()[0]}")
print(f"Django:     {django.get_version()}")
print(f"OpenCV:     {cv2.__version__}")
print(f"MediaPipe:  {mp.__version__}")
PY

echo "==> GestureBoardPro Codespace ready"
