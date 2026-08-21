from __future__ import annotations

import unittest

from experience_evaluation.b_specialization import (
    build_agent_specializations,
    match_agent_family,
)


class BSpecializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = {
            "base_mean_logit": 0.15,
            "prior_sd_logit": 0.75,
            "min_logit": -0.25,
            "max_logit": 0.60,
            "feature_deltas": {
                "coding_agent": {"coding": 0.12},
                "mcp_extensible": {"agentic_coding": 0.10},
                "subagents": {"agentic_coding": 0.12, "reasoning": 0.04},
            },
        }
        self.agents = [
            {
                "agent_id":"codex","display_name":"Codex","aliases":["codex-cli","openai-codex"],
                "features":["coding_agent","mcp_extensible","subagents"],
                "sources":[{"url":"https://github.com/openai/codex","kind":"official_repo"}],
            },
            {
                "agent_id":"openclaw","display_name":"OpenClaw","aliases":["open-claw","clawdbot"],
                "features":["mcp_extensible"],
                "sources":[{"url":"https://github.com/openclaw/openclaw","kind":"official_repo"}],
            },
        ]

    def test_feature_rubric_generates_seven_axis_canonical_prior(self) -> None:
        dataset = build_agent_specializations(self.agents, self.rubric)
        codex = [row for row in dataset["cells"] if row["agent_id"] == "codex"]
        self.assertEqual(len(codex), 7)
        values = {row["dimension_id"]: row["canonical_value"] for row in codex}
        self.assertAlmostEqual(values["coding"], 0.27)
        self.assertAlmostEqual(values["agentic_coding"], 0.37)
        self.assertAlmostEqual(values["reasoning"], 0.19)
        self.assertEqual(values["mathematics"], 0.15)
        self.assertEqual(values["language"], 0.15)
        self.assertTrue(all(row["canonical_scale_id"] == "canonical_logit/v0.1" for row in codex))
        self.assertTrue(all(row["calibration_status"] == "document_specialized_prior_for_c" for row in codex))

    def test_alias_matching_ignores_separate_version_field(self) -> None:
        dataset = build_agent_specializations(self.agents, self.rubric)
        matched = match_agent_family(
            "OpenAI Codex", dataset["agents"], dataset["aliases"], observed_version="999.0"
        )
        self.assertEqual(matched["match_tier"], "stable_family_alias")
        self.assertEqual(matched["agent_id"], "codex")
        self.assertEqual(matched["observed_version"], "999.0")
        self.assertEqual(matched["version_policy"], "ignored_for_matching")

    def test_openclaw_and_unknown_have_explicit_fallbacks(self) -> None:
        dataset = build_agent_specializations(self.agents, self.rubric)
        openclaw = match_agent_family("open-claw", dataset["agents"], dataset["aliases"])
        self.assertEqual(openclaw["agent_id"], "openclaw")
        unknown = match_agent_family("future-agent", dataset["agents"], dataset["aliases"])
        self.assertEqual(unknown["match_tier"], "generic_default")
        self.assertEqual(unknown["agent_id"], "generic-agent-shell")


if __name__ == "__main__":
    unittest.main()
