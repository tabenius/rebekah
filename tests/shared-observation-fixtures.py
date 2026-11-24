#!/usr/bin/env python3
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway", ROOT / "nix/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class SharedObservationsTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "tests/fixtures"
        raw = (directory / "task-observations.v1.json").read_bytes()
        provenance = json.loads((directory / "task-observations.provenance.json").read_text())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), provenance["sha256"])
        self.fixture = json.loads(raw)
        self.assertEqual(self.fixture["schema"], "ragbaz.task-observation-fixtures/v1")
        self.cfg = gw.Config({"REBEKAH_GATEWAY_EXPOSE": "frog", "FROG_HOST": "localhost"})

    def projection(self):
        projection = copy.deepcopy(self.fixture["private_projection"])
        projection["observed_at"] = datetime.now(timezone.utc).isoformat()
        return projection

    def view(self, projection):
        with patch.object(gw, "internal_json", return_value=(200, projection)):
            return gw.v1_frog_kanban(self.cfg)

    def test_statuses_lanes_and_nullability_remain_observations(self):
        for case in self.fixture["statuses"]:
            projection = self.projection()
            projection["cards"][0].update(status=case["status"], lane=case["frog_lane"])
            view = self.view(projection)
            self.assertEqual(view["status"], "ready")
            self.assertEqual(view["items"][0]["status"], case["status"])
            self.assertEqual(view["items"][0]["lane"], case["frog_lane"])
            self.assertIs(view["items"][0]["ready"], False)
            self.assertIsNone(view["items"][0]["assignee"])

    def test_freshness_is_not_refreshed_when_a_view_is_read(self):
        for case in self.fixture["gateway_freshness"]:
            projection = self.projection()
            projection["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=case["age_seconds"])).isoformat()
            view = self.view(projection)
            self.assertEqual(view["status"], case["status"])
            self.assertEqual(view["stale"], case["stale"])

    def test_envelope_and_timestamp_refusals_fail_closed(self):
        projection = self.projection()
        projection["cards"][0]["ready"] = None
        self.assertEqual(self.view(projection)["items"], [])
        for case in self.fixture["gateway_refusals"]:
            projection = self.projection()
            projection[case["field"]] = case["value"]
            self.assertEqual(self.view(projection)["items"], [])
        for value in self.fixture["invalid_timestamps"]:
            projection = self.projection()
            projection["observed_at"] = value
            self.assertEqual(self.view(projection)["status"], "unavailable")
        for value in self.fixture["valid_timestamps"]:
            self.assertIsNotNone(gw._agenda_time(value).tzinfo)


if __name__ == "__main__":
    unittest.main()
