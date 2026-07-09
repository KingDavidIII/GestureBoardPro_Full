"""
GestureBoard Pro
Production Settings
"""

from __future__ import annotations

from .base import *  # noqa: F403

# =============================================================================
# Production Configuration
# =============================================================================

DEBUG = False

SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SECURE = True

SECURE_BROWSER_XSS_FILTER = True

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"
