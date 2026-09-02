#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "==> GestureBoardPro Codespace bootstrap"
echo "Repository: ${ROOT_DIR}"

cd "${ROOT_DIR}"

echo "==> Installing Linux runtime dependencies"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0

echo "==> Preparing Python 3.11 environment"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3.11 -m venv "${VENV_DIR}"
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
