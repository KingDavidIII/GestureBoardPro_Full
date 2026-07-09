"""
GestureBoard Pro
Environment Configuration

Loads and validates environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Project Paths
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BACKEND_DIR = PROJECT_ROOT / "backend"

ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def get_env(
    key: str,
    default: str | None = None,
    *,
    required: bool = False,
) -> str:
    """
    Retrieve an environment variable.

    Args:
        key:
            Environment variable name.

        default:
            Default value if missing.

        required:
            Raise RuntimeError if variable is missing.

    Returns:
        Environment variable value.
    """

    value = os.getenv(key, default)

    if required and value is None:
        raise RuntimeError(f"Missing required environment variable: {key}")

    return value


# -----------------------------------------------------------------------------
# Core Environment
# -----------------------------------------------------------------------------

SECRET_KEY = get_env(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-change-me",
)

TRUE_VALUES = {
    "true",
    "1",
    "yes",
    "on",
}

DEBUG = (
    get_env(
        "DJANGO_DEBUG",
        "True",
    )
    .strip()
    .lower()
    in TRUE_VALUES
)

ENVIRONMENT = get_env(
    "DJANGO_ENV",
    "development",
).lower()

ALLOWED_HOSTS = [
    host.strip()
    for host in get_env(
        "DJANGO_ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
]
