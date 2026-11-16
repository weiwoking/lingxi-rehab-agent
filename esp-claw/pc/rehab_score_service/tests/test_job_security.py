import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud_interface.client_network_policy import (
    install_job_client_network_policy,
    is_job_client_allowed,
)
from cloud_interface.legacy_loopback_guard import install_legacy_loopback_guard


class JobSecurityTests(unittest.TestCase):
    def _app(self):
        app = FastAPI()

        @app.post("/score_trial")
        async def compatibility():
            return {"ok": True}

        @app.get("/api/v1/score/health")
        async def health():
            return {"service": "rehab_score_service", "status": "ready"}

        @app.post("/api/v1/score/jobs")
        async def jobs():
            return {"ok": True}

        install_job_client_network_policy(app)
        install_legacy_loopback_guard(app)
        return app

    def test_job_policy_accepts_only_loopback_and_rfc1918_ipv4(self):
        allowed = (
            "127.0.0.1",
            "127.255.255.254",
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.0.1",
            "192.168.255.254",
        )
        denied = (
            "",
            "localhost",
            "8.8.8.8",
            "172.15.255.255",
            "172.32.0.1",
            "169.254.1.1",
            "::1",
            "fc00::1",
        )
        for host in allowed:
            with self.subTest(host=host):
                self.assertTrue(is_job_client_allowed(host))
        for host in denied:
            with self.subTest(host=host):
                self.assertFalse(is_job_client_allowed(host))

    def test_private_job_and_health_clients_are_allowed(self):
        for path, method in (
            ("/api/v1/score/health", "get"),
            ("/api/v1/score/jobs", "post"),
        ):
            with self.subTest(path=path):
                with TestClient(self._app(), client=("192.168.137.221", 50000)) as client:
                    response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 200)

    def test_public_job_client_is_forbidden(self):
        with TestClient(self._app(), client=("8.8.8.8", 50000)) as client:
            response = client.get("/api/v1/score/health")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "job_client_network_forbidden")

    def test_x_forwarded_for_cannot_bypass_or_deny_policy(self):
        with TestClient(self._app(), client=("8.8.8.8", 50000)) as client:
            denied = client.get(
                "/api/v1/score/health",
                headers={"X-Forwarded-For": "192.168.137.221"},
            )
        with TestClient(self._app(), client=("10.1.2.3", 50000)) as client:
            allowed = client.get(
                "/api/v1/score/health",
                headers={"X-Forwarded-For": "8.8.8.8"},
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_lan_compatibility_post_is_forbidden(self):
        with TestClient(self._app(), client=("192.168.1.50", 50000)) as client:
            response = client.post("/score_trial")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "legacy_endpoint_loopback_only")

    def test_loopback_compatibility_post_remains_compatible(self):
        with TestClient(self._app(), client=("127.0.0.1", 50000)) as client:
            response = client.post("/score_trial")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


if __name__ == "__main__":
    unittest.main()
