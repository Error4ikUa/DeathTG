from __future__ import annotations

import unittest
from unittest.mock import patch

import dtg


class PanelListenerHostTests(unittest.TestCase):
    def test_loopback_panel_adds_only_explicit_tailscale_listener(self) -> None:
        with (
            patch.object(dtg, "effective_panel_bind_host", return_value="127.0.0.1"),
            patch.object(dtg, "tailscale_listener_hosts", return_value=["100.97.243.4"]),
        ):
            self.assertEqual(dtg._panel_listen_hosts(), ["127.0.0.1", "100.97.243.4"])

    def test_explicit_remote_bind_does_not_add_a_second_listener(self) -> None:
        with (
            patch.object(dtg, "effective_panel_bind_host", return_value="192.0.2.10"),
            patch.object(dtg, "tailscale_listener_hosts", return_value=["100.97.243.4"]),
        ):
            self.assertEqual(dtg._panel_listen_hosts(), ["192.0.2.10"])

    def test_port_selection_requires_every_listener_to_be_available(self) -> None:
        hosts = ["127.0.0.1", "100.97.243.4"]

        def available(host: str, port: int) -> bool:
            return port == 8083 or (host == "127.0.0.1" and port == 8082)

        with patch.object(dtg, "_port_is_available", side_effect=available):
            self.assertEqual(dtg._pick_panel_port(hosts, 8082), 8083)


if __name__ == "__main__":
    unittest.main()
