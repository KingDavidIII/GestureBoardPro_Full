"""
GestureBoard Pro
Development Settings
"""

from __future__ import annotations

from .base import *  # noqa: F403

# =============================================================================
# Development Configuration
# =============================================================================

DEBUG = True

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
]

# Email backend

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Disable HTTPS-only cookies during development

SESSION_COOKIE_SECURE = False

CSRF_COOKIE_SECURE = False
