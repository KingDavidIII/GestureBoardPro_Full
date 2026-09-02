#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -x .venv/bin/python ]]; then
    echo "ERROR: .venv is missing."
    exit 1
fi

echo "=== Runtime ==="
.venv/bin/python --version
node --version
npm --version

echo
echo "=== Backend ==="
(
    cd backend
    ../.venv/bin/python manage.py check
    ../.venv/bin/python manage.py makemigrations --check --dry-run
)

echo
echo "=== Frontend ==="
(
    cd frontend
    npm run typecheck
    npm run lint
    npm run format:check
)

echo
echo "GestureBoardPro Codespace verification: PASS"
