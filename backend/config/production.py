"""
GestureBoard Pro
Production Settings
"""

from __future__ import annotations

from .base import *  # noqa: F403

if not SECRET_KEY or SECRET_KEY == "django-insecure-dev-key-change-me":  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY must be configured for production.")

# =============================================================================
# Production Configuration
# =============================================================================

DEBUG = False

SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SECURE = True

SECURE_BROWSER_XSS_FILTER = True

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"
