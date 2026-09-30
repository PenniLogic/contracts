"""Import and usage test of the generated Python client (the smoke consumer, ADR-015 §2)."""

from __future__ import annotations

import json
import unittest

import pennilogic_contracts
from consumer import SyntheticEnvelope, client_for, envelope_from_wire, envelope_to_wire, problem_from_json, total_in_minor_units
from pennilogic_contracts.models import ProblemDetail
from pennilogic_contracts.models.currency_registry import REGISTRY
from pennilogic_contracts.models.money import Money

from fixtures import ROOT


class ImportTest(unittest.TestCase):
    def test_package_imports_and_exposes_wrappers(self) -> None:
        self.assertTrue(hasattr(pennilogic_contracts, "ApiClient"))
        self.assertTrue(hasattr(pennilogic_contracts, "Configuration"))
        self.assertIs(pennilogic_contracts.models.Money, Money)

    def test_client_constructs_without_network(self) -> None:
        client = client_for("api.pennilogic.example")
        self.assertEqual(client.configuration.host, "https://api.pennilogic.example/v1")

    def test_registry_matches_published_file(self) -> None:
        published = json.loads((ROOT / "spec" / "currency-registry.v1.json").read_text(encoding="utf-8"))
        expected = {e["code"]: (e["exponent"], e["minor_unit_name"]) for e in published["currencies"]}
        self.assertEqual({code: (entry.exponent, entry.minor_unit_name) for code, entry in REGISTRY.items()}, expected)

    def test_manifest_records_versions(self) -> None:
        manifest = json.loads((ROOT / "build" / "generated" / "python" / "contracts-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["target"], "python")
        self.assertRegex(manifest["spec_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(manifest["generator"]["version"], "7.25.0")
        self.assertEqual(len(manifest["tree_sha256"]), 64)


class ProblemDetailTest(unittest.TestCase):
    def test_problem_round_trip(self) -> None:
        text = json.dumps({
            "type": "about:blank", "title": "Validation rejected", "status": 422,
            "code": "validation_rejected", "correlation_id": "req-01HZY0000000000000000000",
            "field": "amount", "reason": "scale_mismatch",
        })
        problem = problem_from_json(text)
        self.assertIsInstance(problem, ProblemDetail)
        self.assertEqual(problem.status, 422)
        self.assertEqual(problem.code, "validation_rejected")
        self.assertEqual(json.loads(problem.to_json())["reason"], "scale_mismatch")

    def test_problem_code_pattern_enforced(self) -> None:
        with self.assertRaises(ValueError):
            ProblemDetail(type="about:blank", title="x", status=400, code="Not-A-Code")


class EnvelopeTest(unittest.TestCase):
    def test_envelope_round_trip_keeps_wrapper_types(self) -> None:
        wire = {"total": {"amount": "-1234.56", "currency": "INR"}, "recorded_at": "2026-09-30T04:52:08.439Z"}
        envelope = envelope_from_wire(wire)
        self.assertIsInstance(envelope, SyntheticEnvelope)
        self.assertEqual(total_in_minor_units(envelope), -123456)
        self.assertEqual(envelope.recorded_at.epoch_millis, 1790743928439)
        self.assertEqual(envelope_to_wire(envelope), wire)

    def test_envelope_rejects_float_money(self) -> None:
        with self.assertRaises(ValueError):
            envelope_from_wire({"total": {"amount": -1234.56, "currency": "INR"}, "recorded_at": "2026-09-30T04:52:08.439Z"})


if __name__ == "__main__":
    unittest.main()
