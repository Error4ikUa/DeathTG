from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

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
    def test_template_renderer_uses_starlette_request_first_api(self) -> None:
        request = request_for("127.0.0.1")
        context = {"request": request, "value": "ok"}
        with patch.object(panel.templates, "TemplateResponse", return_value="rendered") as render:
            result = panel._template_response("clean_profile.html", context, status_code=202)
        self.assertEqual(result, "rendered")
        render.assert_called_once_with(
            request,
            "clean_profile.html",
            context,
            status_code=202,
        )

    def test_template_renderer_rejects_missing_request_context(self) -> None:
        with self.assertRaises(RuntimeError):
            panel._template_response("clean_profile.html", {})

    def test_remote_client_cannot_spoof_localhost_host_header(self) -> None:
        request = request_for("192.0.2.40", host="localhost:8080")
        with patch.object(panel, "panel_trust_proxy", return_value=False):
            self.assertFalse(panel._is_local_request(request))

    def test_trusted_proxy_uses_last_forwarded_hop(self) -> None:
        request = request_for("127.0.0.1", host="panel.example")
        request.scope["headers"].append(
            (b"x-forwarded-for", b"127.0.0.1, 198.51.100.77")
        )
        with patch.object(panel, "panel_trust_proxy", return_value=True):
            self.assertEqual(panel._client_ip(request), "198.51.100.77")
            self.assertFalse(panel._is_local_request(request))

    def test_setup_redirect_never_discloses_setup_token(self) -> None:
        request = request_for("192.0.2.40")
        with patch.object(panel, "startup_snapshot", return_value={"setup_required": True}):
            response = panel._auth_guard(request)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")
        self.assertNotIn("setup_token", response.headers["location"])

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
    async def test_request_body_limit_rejects_chunked_oversize_payload(self) -> None:
        reached = False

        async def app(scope, receive, send):
            nonlocal reached
            reached = True
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break

        messages = iter(
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"456", "more_body": False},
            ]
        )
        sent = []

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        middleware = panel.RequestBodyLimitMiddleware(app, max_bytes=4)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/setup/save",
                "headers": [],
            },
            receive,
            send,
        )
        self.assertTrue(reached)
        self.assertEqual(sent[0]["status"], 413)
        headers = dict(sent[0]["headers"])
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")

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
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_anonymous_unsafe_request_without_session_scope_fails_closed(self) -> None:
        request = request_for(
            "192.0.2.40",
            method="POST",
            path="/system/restart",
            host="localhost",
            origin="http://localhost",
        )
        request.scope.pop("session")

        async def unreachable(_request):
            self.fail("Anonymous unsafe request reached the application")

        with patch.object(panel, "_network_access_allowed", return_value=False):
            response = await panel.harden_responses(request, unreachable)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_https_response_gets_hsts(self) -> None:
        request = request_for("127.0.0.1", path="/profile", host="127.0.0.1:8080")
        request.scope["scheme"] = "https"

        async def response_for(_request):
            from starlette.responses import Response

            return Response("ok")

        response = await panel.harden_responses(request, response_for)
        self.assertIn("max-age=31536000", response.headers["strict-transport-security"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_setup_save_is_blocked_after_setup_completed(self) -> None:
        request = request_for(
            "127.0.0.1",
            method="POST",
            path="/setup/save",
            host="127.0.0.1:8080",
            origin="http://127.0.0.1:8080",
        )
        begin = AsyncMock()
        with (
            patch.object(panel, "startup_snapshot", return_value={"setup_required": False}),
            patch.object(panel, "begin_qr_login", begin),
        ):
            response = await panel.setup_save(
                request,
                api_id=12345,
                api_hash="not-a-real-hash",
                session_name="deathtg",
                setup_token="",
            )
        self.assertEqual(response.status_code, 403)
        begin.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
