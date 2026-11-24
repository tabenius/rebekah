#!/usr/bin/env python3
"""Bounded backend responses; no real upstream credentials or network."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("gateway", Path(__file__).resolve().parents[1] / "nix/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class ResponseBoundsTests(unittest.TestCase):
    def test_exact_limit_and_empty_body(self):
        for body in (b"", b"1234"):
            response = Mock()
            response.read.return_value = body
            self.assertEqual(gw.bounded_response_read(response, 4), body)
            response.read.assert_called_once_with(5)

    def test_oversized_body_without_content_length_is_refused(self):
        response = Mock()
        response.read.return_value = b"12345"
        with self.assertRaises(ValueError):
            gw.bounded_response_read(response, 4)
        response.getheader.assert_not_called()

    def test_json_helpers_fail_closed_and_close_connection(self):
        cfg = gw.Config({"REBEKAH_GATEWAY_EXPOSE": "frog", "FROG_HOST": "localhost"})
        for internal in (False, True):
            response = Mock(status=200)
            response.read.return_value = b"x" * (gw.MAX_BACKEND_JSON + 1)
            conn = Mock()
            conn.getresponse.return_value = response
            with patch.object(gw.http.client, "HTTPConnection", return_value=conn):
                result = (gw.internal_json(cfg, "frog", "GET", "/") if internal
                          else gw.backend_get_json(cfg, "frog", "/"))
            self.assertEqual(result, (None, None) if internal else None)
            response.read.assert_called_once_with(gw.MAX_BACKEND_JSON + 1)
            conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
