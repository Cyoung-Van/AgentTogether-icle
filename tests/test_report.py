"""AgentTaskReport: fixed form, parsing, metric derivation, consolidation."""

from __future__ import annotations

import json
import unittest

from icle.report import (
    FORM_FIELD_NAMES,
    METRIC_CONTRACT,
    REPORT_MARKER,
    ReportError,
    apply_measured_consumption,
    blank_report,
    consolidation,
    finishing_consolidation,
    has_consumption,
    merge_reports,
    normalize_report,
    parse_report,
    prompt_block,
    report_metrics,
)

FILLED = {
    "requirements_total": 4,
    "requirements_met": 3,
    "verification_ran": True,
    "verification_passed": True,
    "tests_total": 10,
    "tests_passed": 9,
    "files_changed": ["src/a.py"],
    "commands_run": ["pytest"],
    "blocked": False,
    "blocked_reason": "",
    "summary": "Implemented the parser.",
}


def _entry(task_id: str, order: int, report: dict | None, agent: str = "kimi") -> dict:
    return {
        "task_id": task_id,
        "agent": agent,
        "order": order,
        "report_status": "observed" if report else "missing",
        "report": report,
    }


class FormTemplateTest(unittest.TestCase):
    def test_blank_report_covers_every_field(self):
        blank = blank_report()
        for name in FORM_FIELD_NAMES:
            self.assertIn(name, blank)

    def test_prompt_block_names_marker_and_every_field(self):
        block = prompt_block()
        self.assertIn(REPORT_MARKER, block)
        for name in FORM_FIELD_NAMES:
            self.assertIn(name, block)

    def test_only_declared_metrics_exist(self):
        declared = {metric["metric_id"] for metric in METRIC_CONTRACT}
        self.assertEqual(declared, set(report_metrics(FILLED)))

    def test_user_accept_is_not_scored(self):
        # The baseline probe has no user accept; scoring it would make C an
        # artifact of that gap rather than of the harness.
        declared = {metric["metric_id"] for metric in METRIC_CONTRACT}
        self.assertNotIn("user_accept", declared)

    def test_consumption_is_not_scored(self):
        declared = {metric["metric_id"] for metric in METRIC_CONTRACT}
        self.assertNotIn("input_tokens", declared)
        self.assertNotIn("duration_s", declared)

    def test_no_metric_is_required(self):
        # Every metric is optional so a partial form still contributes; a form
        # with nothing usable is rejected by the engine as no_scored_metrics.
        self.assertEqual([m["metric_id"] for m in METRIC_CONTRACT if m["required"]], [])


class NormalizeTest(unittest.TestCase):
    def test_unknown_fields_are_dropped_and_listed(self):
        report = normalize_report({**FILLED, "self_score": 9.9})
        self.assertNotIn("self_score", report)
        self.assertEqual(report["ignored_fields"], ["self_score"])

    def test_unanswered_field_is_absent_not_zero(self):
        report = normalize_report({"summary": "x", "requirements_total": "n/a"})
        self.assertNotIn("requirements_total", report)

    def test_met_cannot_exceed_total(self):
        report = normalize_report({**FILLED, "requirements_met": 99})
        self.assertEqual(report["requirements_met"], 4)

    def test_verification_passed_dropped_when_not_run(self):
        report = normalize_report({**FILLED, "verification_ran": False})
        self.assertNotIn("verification_passed", report)

    def test_non_object_rejected(self):
        with self.assertRaises(ReportError):
            normalize_report(["not", "an", "object"])


class MetricTest(unittest.TestCase):
    def test_derived_metrics(self):
        metrics = report_metrics(normalize_report(FILLED))
        self.assertEqual(metrics["requirement_coverage"], 0.75)
        self.assertEqual(metrics["verification_passed"], 1.0)
        self.assertEqual(metrics["test_pass_rate"], 0.9)

    def test_missing_verification_yields_no_metric(self):
        report = normalize_report({**FILLED, "verification_ran": False})
        self.assertNotIn("verification_passed", report_metrics(report))

    def test_zero_total_yields_no_ratio(self):
        report = normalize_report({**FILLED, "tests_total": 0, "tests_passed": 0})
        self.assertNotIn("test_pass_rate", report_metrics(report))

    def test_empty_report_yields_nothing(self):
        self.assertEqual(report_metrics(None), {})


class ParseTest(unittest.TestCase):
    def test_marker_form_is_extracted(self):
        output = f"did the work\n{REPORT_MARKER}\n{json.dumps(FILLED)}\n"
        report, status = parse_report(output)
        self.assertEqual(status, "observed")
        self.assertEqual(report["requirements_met"], 3)

    def test_fenced_block_without_marker_is_accepted(self):
        output = "here you go\n```json\n" + json.dumps(FILLED) + "\n```\n"
        report, status = parse_report(output)
        self.assertEqual(status, "observed")
        self.assertEqual(report["tests_passed"], 9)

    def test_last_form_wins_over_an_earlier_example(self):
        first = {**FILLED, "requirements_met": 1}
        output = json.dumps(first) + "\n...prose...\n" + REPORT_MARKER + "\n" + json.dumps(FILLED)
        report, _ = parse_report(output)
        self.assertEqual(report["requirements_met"], 3)

    def test_no_json_is_missing(self):
        self.assertEqual(parse_report("all done, no report")[1], "missing")

    def test_empty_output_is_missing(self):
        self.assertEqual(parse_report("")[1], "missing")

    def test_form_without_any_metric_is_invalid(self):
        payload = {"summary": "x", "blocked": False, "blocked_reason": "", "files_changed": []}
        output = f"{REPORT_MARKER}\n{json.dumps(payload)}"
        report, status = parse_report(output)
        self.assertEqual(status, "invalid")
        self.assertEqual(report_metrics(report), {})

    def test_usage_json_is_not_a_task_report(self):
        usage = {"input_tokens": 1200, "output_tokens": 400, "duration_s": 3.2, "total_tokens": 1600}
        self.assertEqual(parse_report(json.dumps(usage))[1], "missing")

    def test_surrounding_prose_does_not_break_extraction(self):
        output = "Step 1 {not json\n" + REPORT_MARKER + "\n" + json.dumps(FILLED) + "\nBye."
        self.assertEqual(parse_report(output)[1], "observed")


class ConsolidationTest(unittest.TestCase):
    def test_single_agent_uses_its_own_form(self):
        result = finishing_consolidation([_entry("t-1", 1, normalize_report(FILLED))])
        self.assertEqual(result["source"], "single_agent")
        self.assertEqual(result["metrics"]["requirement_coverage"], 0.75)

    def test_finishing_agent_wins_without_intelligence(self):
        early = normalize_report({**FILLED, "requirements_total": 2, "requirements_met": 0})
        late = normalize_report(FILLED)
        result = finishing_consolidation([_entry("t-1", 1, early), _entry("t-2", 2, late)])
        self.assertEqual(result["source"], "finishing_agent")
        self.assertIn("t-2", result["reason"])
        self.assertEqual(result["metrics"]["requirement_coverage"], 0.75)

    def test_finishing_agent_skips_missing_forms(self):
        result = finishing_consolidation([
            _entry("t-1", 1, normalize_report(FILLED)),
            _entry("t-2", 2, None),
        ])
        self.assertEqual(result["reason"], "finishing_agent:t-1")

    def test_no_form_anywhere_is_reported_not_faked(self):
        result = finishing_consolidation([_entry("t-1", 1, None)])
        self.assertEqual(result["source"], "none")
        self.assertIsNone(result["report"])
        self.assertEqual(result["metrics"], {})
        self.assertEqual(result["report_status"], "missing")

    def test_merge_sums_counts_and_ands_flags(self):
        unverified = normalize_report({**FILLED, "verification_passed": False})
        merged = merge_reports([
            _entry("t-1", 1, normalize_report(FILLED)),
            _entry("t-2", 2, unverified),
        ])
        self.assertEqual(merged["requirements_total"], 8)
        self.assertEqual(merged["requirements_met"], 6)
        self.assertEqual(merged["tests_total"], 20)
        self.assertFalse(merged["verification_passed"])

    def test_merge_is_blocked_if_any_subtask_blocked(self):
        blocked = normalize_report({**FILLED, "blocked": True, "blocked_reason": "no network"})
        merged = merge_reports([_entry("t-1", 1, normalize_report(FILLED)), _entry("t-2", 2, blocked)])
        self.assertTrue(merged["blocked"])
        self.assertIn("no network", merged["blocked_reason"])

    def test_merge_without_forms_is_none(self):
        self.assertIsNone(merge_reports([_entry("t-1", 1, None)]))

    def test_consolidation_records_every_input(self):
        entries = [_entry("t-1", 1, normalize_report(FILLED)), _entry("t-2", 2, None)]
        result = consolidation(entries, report=normalize_report(FILLED), source="finishing_agent")
        self.assertEqual([row["report_status"] for row in result["inputs"]], ["observed", "missing"])

    def test_consumption_zero_is_a_claim(self):
        report = normalize_report({**FILLED, "input_tokens": 0, "output_tokens": 0, "duration_s": 0})
        self.assertTrue(has_consumption(report))
        self.assertFalse(has_consumption(normalize_report(FILLED)))

    def test_measured_duration_fills_omitted_time(self):
        report = apply_measured_consumption(
            normalize_report({**FILLED, "input_tokens": 10, "output_tokens": 4}),
            duration_ms=2500,
        )
        self.assertEqual(report["duration_s"], 2.5)
        self.assertTrue(has_consumption(report))

    def test_unknown_source_rejected(self):
        with self.assertRaises(ReportError):
            consolidation([], report=None, source="made_up")


if __name__ == "__main__":
    unittest.main()
