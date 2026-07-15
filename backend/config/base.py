"""
GestureBoard Pro
Base Django Settings

Shared settings for all environments.
"""

from __future__ import annotations

from .environment import (
    ALLOWED_HOSTS as ENV_ALLOWED_HOSTS,
)
from .environment import (
    BACKEND_DIR,
    get_env,
)
from .environment import (
    DEBUG as ENV_DEBUG,
)
from .environment import (
    SECRET_KEY as ENV_SECRET_KEY,
)

# =============================================================================
# Core
# =============================================================================

BASE_DIR = BACKEND_DIR

SECRET_KEY = ENV_SECRET_KEY
DEBUG = ENV_DEBUG
ALLOWED_HOSTS = ENV_ALLOWED_HOSTS

# =============================================================================
# Applications
# =============================================================================

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
]

THIRD_PARTY_APPS = [
    "daphne",
    "channels",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "gestureboard.apps.GestureBoardConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# =============================================================================
# Middleware
# =============================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# =============================================================================
# URL Configuration
# =============================================================================

ROOT_URLCONF = "config.urls"

# =============================================================================
# Templates
# =============================================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "resources" / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =============================================================================
# WSGI / ASGI
# =============================================================================

WSGI_APPLICATION = "config.wsgi.application"

ASGI_APPLICATION = "config.asgi.application"

LOGIN_URL = "/admin/login/"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/"

# =============================================================================
# Database
# =============================================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# =============================================================================
# Password Validation
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.MinimumLengthValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.CommonPasswordValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.NumericPasswordValidator"),
    },
]

# =============================================================================
# Internationalization
# =============================================================================

LANGUAGE_CODE = "en-gb"

TIME_ZONE = "Africa/Lagos"

USE_I18N = True

USE_TZ = True

SITE_ID = 1

# =============================================================================
# Static Files
# =============================================================================

STATIC_URL = "static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "resources" / "static",
]

# =============================================================================
# Media Files
# =============================================================================

MEDIA_URL = "media/"

MEDIA_ROOT = BASE_DIR / "resources" / "media"

# =============================================================================
# Channels
# =============================================================================

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# =============================================================================
# Session settings
# =============================================================================

SESSION_ENGINE = "django.contrib.sessions.backends.db"

SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_HTTPONLY = True

# =============================================================================
# Logging
# =============================================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "gestureboard.recognition.features": {
            "handlers": ["console"],
            "level": get_env("RECOGNITION_FEATURES_LOG_LEVEL", "WARNING").upper(),
            "propagate": False,
        },
    },
}

# =============================================================================
# Security
# =============================================================================

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

# =============================================================================
# Default Primary Key
# =============================================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# End of Settings
# =============================================================================
