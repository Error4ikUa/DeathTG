from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import deathtg.tailscale as tailscale


def _status_payload() -> dict:
    return {
        "BackendState": "Running",
        "Self": {
            "ID": "node-self",
            "HostName": "death-pc",
            "DNSName": "death-pc.example.ts.net.",
            "TailscaleIPs": ["100.90.80.70", "fd7a:115c:a1e0::1"],
            "UserID": 42,
        },
        "MagicDNSSuffix": "example.ts.net",
        "CurrentTailnet": {
            "Name": "owner@example.com",
            "MagicDNSSuffix": "example.ts.net",
        },
        "User": {
            "42": {"LoginName": "owner@example.com"},
            "77": {"LoginName": "guest@example.com"},
        },
        "Peer": {
            "node-key:phone": {
                "HostName": "death-phone",
                "DNSName": "death-phone.example.ts.net.",
                "TailscaleIPs": ["100.90.80.71"],
                "UserID": 42,
                "Online": True,
            },
            "node-key:guest": {
                "HostName": "shared-phone",
                "DNSName": "shared-phone.example.ts.net.",
                "TailscaleIPs": ["100.90.80.72"],
                "UserID": 77,
                "Online": True,
            },
        },
    }


class TailscaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_cache = tailscale._CACHE
        tailscale._CACHE = (0.0, {})
        self.temp_dir = tempfile.TemporaryDirectory()
        self.binding_patch = patch.object(
            tailscale,
            "TAILSCALE_BINDING_PATH",
            Path(self.temp_dir.name) / "tailscale_binding.json",
        )
        self.binding_patch.start()

    def tearDown(self) -> None:
        self.binding_patch.stop()
        self.temp_dir.cleanup()
        tailscale._CACHE = self.previous_cache

    @staticmethod
    def _bound_status() -> dict:
        status = tailscale._build_status(_status_payload(), "tailscale")
        status["identity_bound"] = True
        return status

    def test_build_status_exposes_magicdns_url_and_peer_map(self) -> None:
        with patch.dict(os.environ, {"PANEL_PORT": "8081", "PANEL_TAILSCALE_TRUST": "1"}):
            status = tailscale._build_status(_status_payload(), "tailscale")

        self.assertTrue(status["connected"])
        self.assertEqual(status["serve_url"], "https://death-pc.example.ts.net")
        self.assertEqual(status["url"], "")
        self.assertEqual(status["peer_ips"]["100.90.80.71"]["hostname"], "death-phone")
        self.assertEqual(status["login_name"], "owner@example.com")

    def test_identity_is_persisted_and_account_switch_fails_closed(self) -> None:
        original = tailscale._apply_identity_binding(tailscale._build_status(_status_payload(), "tailscale"))
        switched_payload = _status_payload()
        switched_payload["Self"]["UserID"] = 77
        switched_payload["CurrentTailnet"]["Name"] = "guest@example.com"
        switched = tailscale._apply_identity_binding(tailscale._build_status(switched_payload, "tailscale"))

        self.assertTrue(original["identity_bound"])
        self.assertFalse(switched["identity_bound"])
        self.assertTrue(tailscale.TAILSCALE_BINDING_PATH.is_file())

    def test_peer_must_exist_in_local_tailnet_status(self) -> None:
        status = self._bound_status()
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_TRUST": "1"}),
            patch.object(tailscale, "tailscale_status", return_value=status),
        ):
            peer = tailscale.tailscale_peer("100.90.80.71")
            unknown = tailscale.tailscale_peer("100.99.99.99")

        self.assertIsNotNone(peer)
        self.assertEqual(peer["hostname"], "death-phone")
        self.assertIsNone(unknown)

    def test_shared_tailnet_user_is_not_authorized(self) -> None:
        status = self._bound_status()
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_TRUST": "1"}),
            patch.object(tailscale, "tailscale_status", return_value=status),
        ):
            self.assertIsNone(tailscale.tailscale_peer("100.90.80.72"))

    def test_tailnet_auth_can_be_disabled(self) -> None:
        status = self._bound_status()
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_TRUST": "0"}),
            patch.object(tailscale, "tailscale_status", return_value=status),
        ):
            self.assertIsNone(tailscale.tailscale_peer("100.90.80.71"))

    def test_allowed_hosts_include_magicdns_and_tailscale_ips(self) -> None:
        status = self._bound_status()
        with patch.object(tailscale, "tailscale_status", return_value=status):
            hosts = tailscale.tailscale_allowed_hosts()

        self.assertIn("death-pc.example.ts.net", hosts)
        self.assertIn("100.90.80.70", hosts)

    def test_serve_target_detection_is_port_specific(self) -> None:
        payload = {"Web": {"death-pc.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8081"}}}}}

        self.assertTrue(tailscale._serve_targets_port(payload, 8081))
        self.assertFalse(tailscale._serve_targets_port(payload, 8082))

    def test_serve_is_configured_for_loopback_only(self) -> None:
        status = self._bound_status()
        tailscale._CACHE = (1.0, status)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.dict(
                os.environ,
                {
                    "PANEL_TAILSCALE_SERVE": "1",
                    "PANEL_TAILSCALE_AUTO_SERVE": "1",
                    "PANEL_TAILSCALE_DIRECT": "1",
                },
            ),
            patch.object(tailscale, "tailscale_status", return_value=status),
            patch.object(tailscale, "_run_serve_status", return_value={}),
            patch.object(tailscale.subprocess, "run", return_value=completed) as run,
        ):
            result = tailscale.ensure_tailscale_serve(8082)

        self.assertTrue(result["serve_ready"])
        self.assertEqual(result["url"], "https://death-pc.example.ts.net")
        self.assertEqual(
            run.call_args.args[0],
            ["tailscale", "serve", "--bg", "--yes", "http://127.0.0.1:8082"],
        )

    def test_serve_timeout_falls_back_to_private_direct_listener(self) -> None:
        status = self._bound_status()
        tailscale._CACHE = (1.0, status)
        with (
            patch.dict(
                os.environ,
                {
                    "PANEL_TAILSCALE_SERVE": "1",
                    "PANEL_TAILSCALE_AUTO_SERVE": "1",
                    "PANEL_TAILSCALE_DIRECT": "1",
                },
            ),
            patch.object(tailscale, "tailscale_status", return_value=status),
            patch.object(tailscale, "_run_serve_status", return_value={}),
            patch.object(
                tailscale.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["tailscale", "serve"], 6),
            ),
        ):
            result = tailscale.ensure_tailscale_serve(8082)

        self.assertFalse(result["serve_ready"])
        self.assertTrue(result["direct_ready"])
        self.assertEqual(result["access_mode"], "direct")
        self.assertEqual(result["url"], "http://death-pc.example.ts.net:8082")

    def test_serve_status_timeout_falls_back_to_private_direct_listener(self) -> None:
        status = self._bound_status()
        tailscale._CACHE = (1.0, status)
        with (
            patch.dict(
                os.environ,
                {
                    "PANEL_TAILSCALE_SERVE": "1",
                    "PANEL_TAILSCALE_AUTO_SERVE": "0",
                    "PANEL_TAILSCALE_DIRECT": "1",
                },
            ),
            patch.object(tailscale, "tailscale_status", return_value=status),
            patch.object(
                tailscale,
                "_run_serve_status",
                side_effect=subprocess.TimeoutExpired(["tailscale", "serve", "status"], 5),
            ),
        ):
            result = tailscale.ensure_tailscale_serve(8082)

        self.assertFalse(result["serve_ready"])
        self.assertTrue(result["direct_ready"])
        self.assertEqual(result["url"], "http://death-pc.example.ts.net:8082")

    def test_direct_listener_uses_only_the_local_tailscale_ipv4(self) -> None:
        status = self._bound_status()
        with patch.dict(os.environ, {"PANEL_TAILSCALE_DIRECT": "1"}):
            self.assertEqual(tailscale.tailscale_listener_hosts(status=status), ["100.90.80.70"])


if __name__ == "__main__":
    unittest.main()
