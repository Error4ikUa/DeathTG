from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import deathtg.tailscale as tailscale


def _status_payload() -> dict:
    return {
        "BackendState": "Running",
        "Self": {
            "HostName": "death-pc",
            "DNSName": "death-pc.example.ts.net.",
            "TailscaleIPs": ["100.90.80.70", "fd7a:115c:a1e0::1"],
        },
        "Peer": {
            "node-key:phone": {
                "HostName": "death-phone",
                "DNSName": "death-phone.example.ts.net.",
                "TailscaleIPs": ["100.90.80.71"],
                "UserID": 42,
                "Online": True,
            }
        },
    }


class TailscaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_cache = tailscale._CACHE
        tailscale._CACHE = (0.0, {})

    def tearDown(self) -> None:
        tailscale._CACHE = self.previous_cache

    def test_build_status_exposes_magicdns_url_and_peer_map(self) -> None:
        with patch.dict(os.environ, {"PANEL_PORT": "8081", "PANEL_TAILSCALE_TRUST": "1"}):
            status = tailscale._build_status(_status_payload(), "tailscale")

        self.assertTrue(status["connected"])
        self.assertEqual(status["serve_url"], "https://death-pc.example.ts.net")
        self.assertEqual(status["url"], "")
        self.assertEqual(status["peer_ips"]["100.90.80.71"]["hostname"], "death-phone")

    def test_peer_must_exist_in_local_tailnet_status(self) -> None:
        status = tailscale._build_status(_status_payload(), "tailscale")
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_TRUST": "1"}),
            patch.object(tailscale, "tailscale_status", return_value=status),
        ):
            peer = tailscale.tailscale_peer("100.90.80.71")
            unknown = tailscale.tailscale_peer("100.99.99.99")

        self.assertIsNotNone(peer)
        self.assertEqual(peer["hostname"], "death-phone")
        self.assertIsNone(unknown)

    def test_tailnet_auth_can_be_disabled(self) -> None:
        status = tailscale._build_status(_status_payload(), "tailscale")
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_TRUST": "0"}),
            patch.object(tailscale, "tailscale_status", return_value=status),
        ):
            self.assertIsNone(tailscale.tailscale_peer("100.90.80.71"))

    def test_allowed_hosts_include_magicdns_and_tailscale_ips(self) -> None:
        status = tailscale._build_status(_status_payload(), "tailscale")
        with patch.object(tailscale, "tailscale_status", return_value=status):
            hosts = tailscale.tailscale_allowed_hosts()

        self.assertIn("death-pc.example.ts.net", hosts)
        self.assertIn("100.90.80.70", hosts)

    def test_serve_target_detection_is_port_specific(self) -> None:
        payload = {"Web": {"death-pc.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8081"}}}}}

        self.assertTrue(tailscale._serve_targets_port(payload, 8081))
        self.assertFalse(tailscale._serve_targets_port(payload, 8082))

    def test_serve_is_configured_for_loopback_only(self) -> None:
        status = tailscale._build_status(_status_payload(), "tailscale")
        tailscale._CACHE = (1.0, status)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.dict(os.environ, {"PANEL_TAILSCALE_SERVE": "1"}),
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


if __name__ == "__main__":
    unittest.main()
