#!/usr/bin/env python
"""GestureBoard Pro Django management utility."""

from __future__ import annotations

import os
import sys

DEFAULT_SETTINGS_MODULE: str = "config.settings"


def main() -> None:
    """
    Execute Django management commands.

    Raises:
        ImportError:
            If Django cannot be imported. This usually indicates that
            the virtual environment is inactive or project dependencies
            have not been installed.
    """

    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        DEFAULT_SETTINGS_MODULE,
    )

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django could not be imported.\n"
            "Ensure that:\n"
            "  • The virtual environment is activated.\n"
            "  • Dependencies are installed.\n"
            "  • Django is available in the current interpreter."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
