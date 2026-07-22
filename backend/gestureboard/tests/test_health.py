"""Health endpoint regression coverage."""

from config.version import APPLICATION_VERSION
from django.test import SimpleTestCase


class HealthEndpointTests(SimpleTestCase):
    def test_health_response_reports_the_application_version(self) -> None:
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "project": "GestureBoard Pro",
                "version": APPLICATION_VERSION,
            },
        )
        self.assertEqual(APPLICATION_VERSION, "0.2.0-alpha.1")
