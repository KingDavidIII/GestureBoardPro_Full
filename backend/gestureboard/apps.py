"""
GestureBoard Application Configuration.
"""

from django.apps import AppConfig


class GestureBoardConfig(AppConfig):
    """
    Django configuration for the GestureBoard application.
    """

    default_auto_field = "django.db.models.BigAutoField"

    name = "gestureboard"

    verbose_name = "GestureBoard"

    def ready(self) -> None:
        """
        Execute application startup hooks.

        Reserved for future initialization such as:
            - Signal registration
            - Plugin discovery
            - Background services
        """
        return
