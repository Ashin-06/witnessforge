"""Semantic and adversarial checks for the synthetic reference-app engine."""

import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from witnessforge import core


def witness():
    return [
        {"id": "a", "op": "send", "message_id": "original", "sender": "Ana", "recipient": "Ben", "text": "First"},
        {"id": "b", "op": "forward", "message_id": "forwarded", "source_id": "original", "sender": "Ben", "recipient": "Cam"},
        {"id": "c", "op": "edit", "message_id": "forwarded", "text": "Second"},
        {"id": "d", "op": "delete", "message_id": "forwarded"},
    ]


class GroundTruthTests(unittest.TestCase):
    def test_known_expected_tombstone_is_not_derived_from_parser(self):
        result = core.inspect_trace(witness())
        target = next(row for row in result["expected"] if row["message_id"] == "forwarded")
        self.assertEqual(target, {"message_id": "forwarded", "sender": "Ben", "recipient": "Cam", "text": None,
                                  "source_id": "original", "edited": True, "deleted": True, "reactions": []})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["signature"], "FIELD_SENDER:forwarded")
        self.assertEqual(result["findings"], [{"code": "FIELD_SENDER", "message_id": "forwarded", "expected": "Ben", "actual": "Ana"}])

    def test_bug_requires_new_schema_and_all_three_operations(self):
        self.assertEqual(core.inspect_trace(witness(), "1")["status"], "PASS")
        for version in ("2", "3"):
            with self.subTest(version=version):
                self.assertEqual(core.inspect_trace(witness(), version)["status"], "FAIL")
                self.assertEqual(core.inspect_trace(witness()[:3], version)["status"], "PASS")
                self.assertEqual(core.inspect_trace([witness()[0], witness()[1], witness()[3]], version)["status"], "PASS")

    def test_edit_copy_semantics_and_reactions_are_checked(self):
        events = witness()[:1] + [
            {"id": "source-edit", "op": "edit", "message_id": "original", "text": "Revised \u20b9"},
            witness()[1],
            {"id": "source-edit-again", "op": "edit", "message_id": "original", "text": "Later"},
            {"id": "reaction", "op": "react", "message_id": "forwarded", "sender": "Cam", "text": "\U0001f44d"},
        ]
        for version in core.VERSIONS:
            result = core.inspect_trace(events, version, "fixed")
            self.assertEqual(result["status"], "PASS")
            forward = next(row for row in result["actual"] if row["message_id"] == "forwarded")
            self.assertEqual(forward["text"], "Revised \u20b9")
            self.assertEqual(forward["reactions"], [{"sender": "Cam", "text": "\U0001f44d"}])

    def test_real_database_layouts_and_preserved_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in core.VERSIONS:
                result = core.inspect_trace(witness(), version, "fixed", Path(directory))
                path = Path(result["database_path"])
                self.assertTrue(path.is_file())
                self.assertEqual(core.parse_fixed(path), result["expected"])
                with closing(sqlite3.connect(path)) as db:
                    stored_version = db.execute("SELECT schema_version FROM reference_app").fetchone()[0]
                    self.assertEqual(stored_version, version)
            one = core.inspect_trace(witness(), "2", "fixed", Path(directory))
            two = core.inspect_trace(witness(), "2", "fixed", Path(directory))
            self.assertNotEqual(one["database_path"], two["database_path"])
        self.assertIsNone(core.inspect_trace(witness())["database_path"])

    def test_database_replay_refuses_to_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.sqlite"
            path.write_bytes(b"preserve me")
            with self.assertRaises(FileExistsError):
                core.replay_to_database(witness(), "2", path)
            self.assertEqual(path.read_bytes(), b"preserve me")

    def test_custom_parser_output_order_is_not_semantic(self):
        def reordered(path):
            return list(reversed(core.parse_fixed(path)))
        self.assertEqual(core.inspect_trace(witness(), parser=reordered)["status"], "PASS")

    def test_custom_parser_malformed_output_and_errors_unresolved(self):
        for parser in (lambda path: {}, lambda path: [{"message_id": "fake"}], lambda path: core.parse_fixed(path) * 2):
            with self.subTest(parser=parser):
                result = core.inspect_trace(witness(), parser=parser)
                self.assertEqual(result["status"], "UNRESOLVED")
                self.assertIsNone(result["signature"])

        def broken(path):
            raise RuntimeError("unsupported database")
        result = core.inspect_trace(witness(), parser=broken)
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIn("unsupported database", result["error"])

    def test_non_target_semantic_failure_is_detected(self):
        def bad_content(path):
            rows = core.parse_fixed(path)
            rows[0]["recipient"] = "Wrong recipient"
            return rows
        result = core.inspect_trace(witness(), parser=bad_content)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"][0]["code"], "FIELD_RECIPIENT")

    def test_evidence_mutating_parser_is_unresolved(self):
        def mutating(path):
            rows = core.parse_fixed(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE reference_app SET scope='tampered'")
            return rows
        result = core.inspect_trace(witness(), parser=mutating)
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIn("modified the evidence", result["error"])


class MinimizationTests(unittest.TestCase):
    def test_demo_reduces_to_valid_four_action_witness(self):
        events = core.demo_trace()
        result = core.minimize_trace(events)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["original_count"], 20)
        self.assertEqual(result["minimized_count"], 4)
        self.assertEqual([a["op"] for a in result["minimized_trace"]], ["send", "forward", "edit", "delete"])
        self.assertTrue(result["one_minimal"])
        self.assertFalse(result["budget_exhausted"])
        self.assertEqual(result["initial_inspection"]["signature"], result["final_inspection"]["signature"])
        core.validate_trace(result["minimized_trace"])
        self.assertLessEqual(result["evaluations"], 200)
        json.dumps(result)

    def test_dependencies_are_never_orphaned_in_trials(self):
        seen_sizes = []
        def checking_parser(path):
            rows = core.parse_buggy(path)
            by_id = {row["message_id"]: row for row in rows}
            for row in rows:
                if row["source_id"]:
                    self.assertIn(row["source_id"], by_id)
            seen_sizes.append(len(rows))
            return rows
        result = core.minimize_trace(core.demo_trace(), parser=checking_parser)
        self.assertEqual(result["minimized_count"], 4)
        self.assertGreater(len(set(seen_sizes)), 1)

    def test_cannot_hop_to_another_failure(self):
        events = witness()
        other = copy.deepcopy(witness())
        for action in other:
            action["id"] = "z-" + action["id"]
            action["message_id"] = "z-" + action["message_id"]
            if "source_id" in action:
                action["source_id"] = "z-" + action["source_id"]
        result = core.minimize_trace(events + other)
        self.assertEqual(result["signature"], "FIELD_SENDER:forwarded")
        self.assertEqual(result["minimized_count"], 4)
        self.assertIn("forwarded", {a["message_id"] for a in result["minimized_trace"]})

    def test_input_and_demo_are_not_mutated(self):
        events = core.demo_trace()
        before = copy.deepcopy(events)
        core.inspect_trace(events)
        core.minimize_trace(events)
        self.assertEqual(events, before)
        events[0]["text"] = "mutated externally"
        self.assertNotEqual(core.demo_trace()[0]["text"], "mutated externally")

    def test_budget_counts_repeats_and_never_claims_minimality(self):
        result = core.minimize_trace(core.demo_trace(), repeats=2, max_evaluations=2)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["evaluations"], 2)
        self.assertEqual(result["minimized_count"], 20)
        self.assertTrue(result["budget_exhausted"])
        self.assertFalse(result["one_minimal"])
        result = core.minimize_trace(core.demo_trace(), repeats=2, max_evaluations=1)
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertEqual(result["evaluations"], 0)

    def test_inconsistent_repeats_are_unresolved(self):
        calls = 0
        def flaky(path):
            nonlocal calls
            calls += 1
            return core.parse_buggy(path) if calls % 2 else core.parse_fixed(path)
        result = core.minimize_trace(core.demo_trace(), parser=flaky)
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertEqual(result["minimized_count"], 20)
        self.assertFalse(result["one_minimal"])
        self.assertIsNone(result["signature"])

    def test_unresolved_candidates_are_not_accepted_or_certified(self):
        def full_trace_only(path):
            rows = core.parse_buggy(path)
            if len(rows) < 9:
                raise RuntimeError("candidate unsupported")
            return rows
        result = core.minimize_trace(core.demo_trace(), parser=full_trace_only)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["one_minimal"])
        self.assertEqual(result["final_inspection"]["signature"], result["signature"])

    def test_passing_trace_is_not_minimized_as_failure(self):
        result = core.minimize_trace(core.demo_trace(), parser="fixed")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["minimized_count"], 20)
        self.assertFalse(result["one_minimal"])
        self.assertEqual(result["evaluations"], 2)


class ValidationTests(unittest.TestCase):
    def test_malformed_traces_are_rejected(self):
        bad = [None, {}, [3], [{"id": "x", "op": "unknown"}], [witness()[1]],
               [witness()[0], witness()[0]], witness() + [witness()[2]],
               [{**witness()[0], "typo": "x"}], [{**witness()[0], "text": "x" * 10001}],
               [{**witness()[0], "sender": ""}], core.demo_trace() * 26]
        for events in bad:
            with self.subTest(events=str(events)[:60]):
                with self.assertRaises(ValueError):
                    core.validate_trace(events)

    def test_invalid_options(self):
        for options in ({"version": "4"}, {"parser": "unknown"}, {"repeats": 0},
                        {"repeats": True}, {"max_evaluations": 0}, {"max_evaluations": 10001}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    core.minimize_trace(witness(), **options)

    def test_empty_trace_is_valid_and_passes(self):
        core.validate_trace([])
        self.assertEqual(core.inspect_trace([])["status"], "PASS")

    def test_fixed_parser_passes_heldout_all_versions(self):
        result = core.validate_repair()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["summary"]["total"], 27)
        self.assertEqual(result["summary"]["passed"], 27)
        self.assertEqual(result["summary"]["buggy_failures"], 8)
        self.assertEqual({item["version"] for item in result["results"]}, {"1", "2", "3"})
        buggy = core.validate_repair("buggy")
        self.assertEqual(buggy["status"], "FAIL")
        self.assertEqual(buggy["summary"]["failed"], 8)
        json.dumps(result)

    def test_repair_validates_more_than_the_target_failure(self):
        def wrong_recipient(path):
            rows = core.parse_fixed(path)
            for row in rows:
                row["recipient"] = "wrong"
            return rows
        result = core.validate_repair(wrong_recipient)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["summary"]["passed"], 3)
        self.assertEqual(result["summary"]["failed"], 24)


if __name__ == "__main__":
    unittest.main()
