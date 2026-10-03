"""Actual generated T-CON-10 client conformance, with no backend effects or real data."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from fixtures import load
from pennilogic_contracts.import_contract import (
    commit_request_from_wire, commit_result_from_wire, dedup_from_wire, preview_from_wire,
    verify_commit, verify_mapping, verify_replay,
)
from pennilogic_contracts.import_policy import MAX_IMPORT_ROWS, SOURCE_PRECEDENCE, source_window
from pennilogic_contracts.models import ConfidenceBand, DedupOutcomeKind, DedupWindow, ImportColumnTarget
from pennilogic_contracts.models.service_problem_detail import ServiceProblemDetail

DATA = load("import-provider.v1.json")


def mutate(value: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    target = result
    for member in change["path"][:-1]:
        target = target[member]
    if change.get("remove"):
        del target[change["path"][-1]]
    else:
        target[change["path"][-1]] = change["value"]
    return result


def decode(target: str, wire: dict[str, Any]) -> object:
    if target == "preview":
        return preview_from_wire(wire)
    if target == "request":
        return commit_request_from_wire(wire)
    if target == "result":
        return commit_result_from_wire(wire)
    return dedup_from_wire(wire)


class ImportProviderTest(unittest.TestCase):
    def test_generated_preview_commit_and_row_error_round_trip(self) -> None:
        preview = preview_from_wire(DATA["preview"])
        request = commit_request_from_wire(DATA["request"])
        result = commit_result_from_wire(DATA["result"])
        verify_commit(preview, result)
        self.assertEqual(request.preview_ref, preview.preview_ref)
        self.assertEqual(json.loads(preview.to_json()), DATA["preview"])
        self.assertEqual(json.loads(result.to_json()), DATA["result"])
        self.assertEqual(preview.rows[0].confidence_band, ConfidenceBand.HIGH)
        self.assertEqual(preview.mapping.user_overrides[0].target, ImportColumnTarget.VALUE_DATE)

    def test_all_dedup_states_are_typed_and_reversible(self) -> None:
        for name in ("screen", "suspected", "linked", "reversed", "review", "clear"):
            outcome = dedup_from_wire(DATA[name])
            self.assertIsInstance(outcome.outcome, DedupOutcomeKind)
            self.assertEqual(json.loads(outcome.to_json()), DATA[name])

    def test_every_schema_and_semantic_negative_is_rejected_without_disclosure(self) -> None:
        for change in DATA["invalid"]:
            with self.subTest(name=change["name"]):
                with self.assertRaises(ValueError) as caught:
                    decode(change["target"], mutate(DATA[change["target"]], change))
                self.assertNotIn("SYNTHETIC_", str(caught.exception))
                self.assertNotIn("12.34", str(caught.exception))
                self.assertNotIn("internal-record-42", str(caught.exception))

    def test_replay_preserves_original_receipt_and_rejects_new_fact_or_metadata(self) -> None:
        original = commit_result_from_wire(DATA["result"])
        verify_replay(original, commit_result_from_wire(copy.deepcopy(DATA["result"])))
        for change in DATA["replay_changes"]:
            with self.subTest(name=change["name"]):
                replay = commit_result_from_wire(mutate(DATA["result"], change))
                with self.assertRaisesRegex(ValueError, "replay_changed"):
                    verify_replay(original, replay)

    def test_closed_confidence_and_complete_symmetric_source_windows(self) -> None:
        self.assertEqual({band.value for band in ConfidenceBand}, {"high", "low"})
        for left in SOURCE_PRECEDENCE:
            for right in SOURCE_PRECEDENCE:
                self.assertIsInstance(source_window(left, right), DedupWindow)
                self.assertEqual(source_window(left, right), source_window(right, left))
        for bad in ("medium", "HIGH", 85, None):
            with self.assertRaises(ValueError):
                ConfidenceBand.from_wire(bad)

    def test_incomplete_mapping_and_unresolved_review_block_commit(self) -> None:
        preview = preview_from_wire(DATA["preview"])
        incomplete = copy.deepcopy(DATA["preview"])
        incomplete["mapping"]["detected_columns"].pop(1)
        incomplete["mapping"]["unmapped_columns"] = [2, 4]
        partial = preview_from_wire(incomplete)
        with self.assertRaisesRegex(ValueError, "mapping_incomplete"):
            verify_mapping(partial.mapping, complete=True)
        review = copy.deepcopy(DATA["preview"])
        review["rows"][0].update(action="review", reason="needs_review", confidence_band="low")
        review["counts"].update(create_count=0, review_count=1)
        with self.assertRaisesRegex(ValueError, "preview_binding"):
            verify_commit(preview_from_wire(review), commit_result_from_wire(DATA["result"]))
        verify_mapping(preview.mapping, complete=True)

    def test_row_bound_is_10000_and_10001_fails(self) -> None:
        wire = copy.deepcopy(DATA["preview"])
        row = wire["rows"][0]
        wire["rows"] = [{**row, "source_row": index + 1} for index in range(MAX_IMPORT_ROWS)]
        wire["counts"] = {"row_count": MAX_IMPORT_ROWS, "create_count": MAX_IMPORT_ROWS, "skip_count": 0, "reject_count": 0, "review_count": 0}
        self.assertEqual(len(preview_from_wire(wire).rows), 10000)
        wire["rows"].append({**row, "source_row": 10001})
        with self.assertRaises(ValueError):
            preview_from_wire(wire)

    def test_override_denial_uses_the_400_binding_without_echoing_a_record(self) -> None:
        problem = ServiceProblemDetail.from_dict(DATA["override_rejection"])
        self.assertEqual(problem.status, 400)
        self.assertEqual(json.loads(problem.to_json()), DATA["override_rejection"])
        self.assertNotIn("matched_record_id", problem.to_dict())

    def test_commit_cannot_upgrade_a_skip_or_substitute_the_reviewed_match(self) -> None:
        for case in DATA["decision_changes"]:
            preview, result = copy.deepcopy(DATA["preview"]), copy.deepcopy(DATA["result"])
            for key in ("preview_change", "counts_change"):
                if key in case:
                    preview = mutate(preview, case[key])
            if "result_change" in case:
                result = mutate(result, case["result_change"])
            with self.assertRaisesRegex(ValueError, "preview_decision"):
                verify_commit(preview_from_wire(preview), commit_result_from_wire(result))


if __name__ == "__main__":
    unittest.main()
