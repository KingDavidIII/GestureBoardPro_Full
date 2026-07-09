"""
GestureBoard Pro
Root URL Configuration
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health_check(request):
    """
    Basic health endpoint.

    Used for:
    - Development testing
    - Docker health checks
    - Future monitoring
    """
    return JsonResponse(
        {
            "status": "ok",
            "project": "GestureBoard Pro",
            "version": "0.1.0-alpha.1",
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "api/",
        include("gestureboard.api.urls"),
    ),
    path(
        "health/",
        health_check,
        name="health",
    ),
]
