from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.requests import Request

import deathtg.panel.clean_app as panel


def request_for(
    ip: str,
    *,
    method: str = "GET",
    path: str = "/",
    host: str = "panel.example",
    origin: str = "",
) -> Request:
    headers = [(b"host", host.encode("ascii"))]
    if origin:
        headers.append((b"origin", origin.encode("ascii")))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": (ip, 45000),
            "server": ("127.0.0.1", 8080),
            "root_path": "",
            "session": {},
        }
    )


class PanelSecurityTests(unittest.TestCase):
    def test_remote_client_cannot_spoof_localhost_host_header(self) -> None:
        request = request_for("192.0.2.40", host="localhost:8080")
        with patch.object(panel, "panel_trust_proxy", return_value=False):
            self.assertFalse(panel._is_local_request(request))

    def test_remote_access_requires_tailnet_or_explicit_public_mode(self) -> None:
        request = request_for("192.0.2.40")
        with (
            patch.object(panel, "tailscale_peer", return_value=None),
            patch.object(panel, "public_panel_enabled", return_value=False),
        ):
            self.assertFalse(panel._network_access_allowed(request))

    def test_tailnet_peer_is_allowed_without_public_mode(self) -> None:
        request = request_for("100.90.80.71")
        with (
            patch.object(panel, "tailscale_peer", return_value={"hostname": "phone"}),
            patch.object(panel, "public_panel_enabled", return_value=False),
        ):
            self.assertTrue(panel._network_access_allowed(request))

    def test_legacy_auth_cookie_without_active_device_is_rejected(self) -> None:
        request = request_for("127.0.0.1")
        request.session["auth"] = True

        response = panel._auth_guard(request)

        self.assertEqual(response.status_code, 303)
        self.assertIn("Device+session+revoked", response.headers["location"])
        self.assertFalse(request.session)

    def test_cross_origin_post_is_rejected(self) -> None:
        request = request_for(
            "127.0.0.1",
            method="POST",
            path="/modules/update-all",
            host="127.0.0.1:8080",
            origin="https://attacker.example",
        )
        self.assertFalse(panel._same_origin_request(request))

    def test_same_origin_post_is_accepted(self) -> None:
        request = request_for(
            "127.0.0.1",
            method="POST",
            path="/modules/update-all",
            host="127.0.0.1:8080",
            origin="http://127.0.0.1:8080",
        )
        self.assertTrue(panel._same_origin_request(request))


class PanelSecurityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_response_keeps_security_headers(self) -> None:
        request = request_for(
            "127.0.0.1",
            method="POST",
            path="/system/update/check",
            host="127.0.0.1:8080",
            origin="https://attacker.example",
        )

        async def unreachable(_request):
            self.fail("Blocked request reached the application")

        response = await panel.harden_responses(request, unreachable)

        self.assertEqual(response.status_code, 403)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
