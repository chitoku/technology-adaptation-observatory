"""Validate the observation contract with the optional test dependency."""

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tao.replay import replay

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA = FIXTURES.parent.parent / "schemas" / "observation-v1.0.0.schema.json"


class ObservationSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def test_expected_and_replayed_observations_match_schema(self):
        self.validator.validate(json.loads((FIXTURES / "home.observation.json").read_bytes()))
        self.validator.validate(replay((FIXTURES / "home.snapshot.json").read_bytes()))

    def test_uncollected_observation_matches_schema(self):
        snapshot = json.loads((FIXTURES / "home.snapshot.json").read_bytes())
        snapshot.update(
            access="not_collected", final_url=None, http_status=None,
            body_kind="unavailable", html=None,
        )
        self.validator.validate(replay(json.dumps(snapshot).encode("utf-8")))

    def test_schema_rejects_raw_html_and_invalid_evidence_state(self):
        observation = json.loads((FIXTURES / "home.observation.json").read_bytes())
        observation["capture"]["html"] = "<html>Raw capture</html>"
        observation["evidence"][0]["state"] = "unrecorded"

        errors = {tuple(error.absolute_path) for error in self.validator.iter_errors(observation)}
        self.assertIn(("capture",), errors)
        self.assertIn(("evidence", 0, "state"), errors)

    def test_schema_validates_capture_timestamp_format(self):
        observation = json.loads((FIXTURES / "home.observation.json").read_bytes())
        observation["capture"]["captured_at"] = "2026-02-30T00:00:00Z"
        errors = list(self.validator.iter_errors(observation))
        self.assertEqual([tuple(error.absolute_path) for error in errors], [
            ("capture", "captured_at"),
        ])
