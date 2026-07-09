"""
GestureBoard Pro

Environment-aware Django settings loader.
"""

from __future__ import annotations

from .environment import ENVIRONMENT

if ENVIRONMENT == "production":
    from .production import *  # noqa: F403
else:
    from .development import *  # noqa: F403
