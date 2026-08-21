from __future__ import annotations

import unittest

from experience_evaluation.freshness import (
    classify_latest_records,
    link_current_model_configs,
)


class FreshnessTests(unittest.TestCase):
    def test_latest_record_is_active_and_older_record_is_superseded(self) -> None:
        rows = [
            {"id":"old","model":"m","task":"t","result_date":"2026-01-01"},
            {"id":"new","model":"m","task":"t","result_date":"2026-08-01"},
            {"id":"unknown","model":"x","task":"t","result_date":None},
        ]
        result = classify_latest_records(
            rows,
            key_fields=("model", "task"),
            as_of="2026-08-20",
            max_age_days=120,
            date_fields=("result_date",),
            policy_id="test/v1",
        )
        statuses = {row["id"]: row["freshness_status"] for row in result["all_records"]}
        self.assertEqual(statuses["new"], "active_current")
        self.assertEqual(statuses["old"], "superseded")
        self.assertEqual(statuses["unknown"], "unknown_date")
        self.assertEqual([row["id"] for row in result["current"]], ["new"])

    def test_latest_record_outside_window_is_stale(self) -> None:
        result = classify_latest_records(
            [{"id":"old","model":"m","result_date":"2025-01-01"}],
            key_fields=("model",),as_of="2026-08-20",max_age_days=120,
            date_fields=("result_date",),policy_id="test/v1",
        )
        self.assertEqual(result["all_records"][0]["freshness_status"], "stale")
        self.assertEqual(result["current"], [])

    def test_model_configs_link_only_to_unique_current_catalog_prefix(self) -> None:
        configs = [
            {"normalized_model_config_id":"openai-gpt-5-6-terra-max"},
            {"normalized_model_config_id":"anthropic-claude-opus-4-5-high"},
        ]
        catalog = [
            {"model_id":"openai-gpt-5-6-terra","status":"current"},
            {"model_id":"anthropic-claude-opus-4-8","status":"current"},
        ]
        linked = link_current_model_configs(configs, catalog)
        by_id = {row["normalized_model_config_id"]: row for row in linked}
        self.assertEqual(by_id["openai-gpt-5-6-terra-max"]["catalog_link_status"], "linked_current_catalog")
        self.assertEqual(by_id["openai-gpt-5-6-terra-max"]["base_model_id"], "openai-gpt-5-6-terra")
        self.assertEqual(by_id["anthropic-claude-opus-4-5-high"]["catalog_link_status"], "unlinked_current_catalog")


if __name__ == "__main__":
    unittest.main()
