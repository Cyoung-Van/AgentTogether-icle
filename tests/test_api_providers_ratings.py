"""UI-16..19: providers + ratings API tests (TestClient, write path)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from icle.api.app import create_app  # noqa: E402
from icle.episode import create_episode  # noqa: E402

MODELS_PAYLOAD = json.dumps({"data": [{"id": "kimi-k3"}, {"id": "kimi-k2"}]}).encode("utf-8")


class ModelsServer(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/models" or self.headers.get("Authorization") not in {"Bearer sk-api-key", "Bearer local"}:
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(MODELS_PAYLOAD)

    def log_message(self, *args):
        pass


class ProvidersRatingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp())
        server = HTTPServer(("127.0.0.1", 0), ModelsServer)
        cls.server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        # each test gets its own store so ratings/marks never bleed across tests
        store = self.root / f"store-{id(self)}"
        project = store / "project"
        project.mkdir(parents=True)
        revision = {
            "schema_version": "icle-agent-revision/v0.1",
            "agent_id": "kimi",
            "revision_id": "test",
            "model": "m", "cli": "c", "provider": "p",
            "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
            "execution_provider": "direct_cli",
            "created_at": "2026-01-01T00:00:00Z",
        }
        self.episode = create_episode(
            store, session_id="session", agent_revision=revision,
            project_id="demo", project_path=project, user_request="test task",
        )
        self.app = create_app(store=store)
        self.client = TestClient(self.app)

    def _make_provider(self, api_key: str = "sk-api-key", roles: list[str] | None = None) -> dict:
        response = self.client.post(
            "/api/providers",
            json={
                "display_name": "Kimi",
                "type": "openai-compatible",
                "base_url": self.base_url,
                "api_key": api_key,
                "roles": roles or ["routing", "judging"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["provider"]

    def test_provider_crud_via_api(self) -> None:
        provider = self._make_provider()
        provider_id = provider["provider_id"]
        self.assertTrue(provider["configured"])
        self.assertNotIn("sk-api-key", response_json := json.dumps(provider))

        listing = self.client.get("/api/providers").json()["providers"]
        self.assertEqual(len(listing), 1)
        self.assertNotIn("sk-api-key", json.dumps(listing))

        patched = self.client.patch(
            f"/api/providers/{provider_id}", json={"display_name": "Kimi K3"}
        ).json()["provider"]
        self.assertEqual(patched["display_name"], "Kimi K3")

        deleted = self.client.delete(f"/api/providers/{provider_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/providers").json()["providers"], [])

    def test_provider_invalid_or_unsupported_type_rejected(self) -> None:
        for provider_type in ("magic", "anthropic-compatible", "cli"):
            response = self.client.post(
                "/api/providers",
                json={"display_name": "X", "type": provider_type, "base_url": "https://example.com"},
            )
            self.assertEqual(response.status_code, 400, provider_type)
        custom = self.client.post(
            "/api/providers",
            json={"display_name": "Custom", "type": "custom", "base_url": "https://example.invalid"},
        )
        self.assertEqual(custom.status_code, 200)

    def test_provider_test_and_models(self) -> None:
        provider = self._make_provider()
        tested = self.client.post(f"/api/providers/{provider['provider_id']}/test")
        self.assertEqual(tested.status_code, 200)
        body = tested.json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "connected")
        # models auto-synced into the store (connection → models in)
        self.assertTrue(body["models_synced"])
        self.assertIn("provider", body)
        self.assertNotEqual(body["provider"]["models"], [])

        models = self.client.get(f"/api/providers/{provider['provider_id']}/models")
        self.assertEqual(models.status_code, 200)
        ids = [m["id"] for m in models.json()["models"]]
        self.assertEqual(ids, ["kimi-k3", "kimi-k2"])

    def test_local_provider_without_key(self) -> None:
        response = self.client.post(
            "/api/providers",
            json={
                "display_name": "Local",
                "type": "local",
                "base_url": self.base_url,
                "roles": ["general"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        provider = response.json()["provider"]
        self.assertTrue(provider["configured"])
        tested = self.client.post(f"/api/providers/{provider['provider_id']}/test").json()
        self.assertTrue(tested["ok"], tested)
        self.assertTrue(tested["models_synced"])

    def test_provider_test_wrong_key(self) -> None:
        provider = self._make_provider(api_key="sk-wrong")
        body = self.client.post(f"/api/providers/{provider['provider_id']}/test").json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "unavailable")

    def test_global_intelligence_model_switch_is_validated_and_persisted(self) -> None:
        provider = self._make_provider(roles=["general"])
        tested = self.client.post(f"/api/providers/{provider['provider_id']}/test")
        self.assertTrue(tested.json()["ok"], tested.text)

        choices = self.client.get("/api/intelligence-models")
        self.assertEqual(choices.status_code, 200, choices.text)
        self.assertEqual([item["model_id"] for item in choices.json()["models"]], ["kimi-k3", "kimi-k2"])
        self.assertTrue(choices.json()["models"][0]["selected"])
        self.assertNotIn("sk-api-key", choices.text)
        self.assertNotIn(self.base_url, choices.text)

        switched = self.client.post(
            "/api/intelligence-model",
            json={"provider_id": provider["provider_id"], "model_id": "kimi-k2"},
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(switched.json()["current"]["model_id"], "kimi-k2")
        self.assertTrue(next(item for item in switched.json()["models"] if item["model_id"] == "kimi-k2")["selected"])
        status = self.client.get("/api/intelligence-status").json()
        self.assertEqual(status["provider"]["display_name"], "Kimi")
        self.assertEqual(status["provider"]["provider_id"], provider["provider_id"])
        self.assertEqual(status["provider"]["model"], "kimi-k2")
        listing = self.client.get("/api/providers").json()["providers"]
        self.assertEqual(listing[0]["selected_model"], "kimi-k2")

        provider_page_switch = self.client.patch(
            f"/api/providers/{provider['provider_id']}", json={"selected_model": "kimi-k3"}
        )
        self.assertEqual(provider_page_switch.status_code, 200, provider_page_switch.text)
        self.assertEqual(self.client.get("/api/intelligence-status").json()["provider"]["model"], "kimi-k3")
        self.assertEqual(
            self.client.get("/api/intelligence-models").json()["current"]["model_id"],
            "kimi-k3",
        )

        invalid = self.client.post(
            "/api/intelligence-model",
            json={"provider_id": provider["provider_id"], "model_id": "not-whitelisted"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.get("/api/intelligence-status").json()["provider"]["model"], "kimi-k3")

        deleted = self.client.delete(f"/api/providers/{provider['provider_id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get("/api/intelligence-models").json()["models"], [])
        self.assertFalse(self.client.get("/api/intelligence-status").json()["provider"]["configured"])

    def test_global_intelligence_model_switch_rejected_while_busy(self) -> None:
        provider = self._make_provider(roles=["general"])
        self.assertTrue(self.client.post(f"/api/providers/{provider['provider_id']}/test").json()["ok"])
        operation_id = self.app.state.intelligence_status.start("plan_task")
        try:
            response = self.client.post(
                "/api/intelligence-model",
                json={"provider_id": provider["provider_id"], "model_id": "kimi-k2"},
            )
        finally:
            self.app.state.intelligence_status.finish(operation_id, outcome="completed")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("current intelligence request", response.json()["detail"])

        operation_id = self.app.state.intelligence_status.start("plan_task")
        try:
            provider_response = self.client.patch(
                f"/api/providers/{provider['provider_id']}", json={"selected_model": "kimi-k2"}
            )
        finally:
            self.app.state.intelligence_status.finish(operation_id, outcome="completed")
        self.assertEqual(provider_response.status_code, 409, provider_response.text)

        operation_id = self.app.state.intelligence_status.start("plan_task")
        try:
            delete_response = self.client.delete(f"/api/providers/{provider['provider_id']}")
        finally:
            self.app.state.intelligence_status.finish(operation_id, outcome="completed")
        self.assertEqual(delete_response.status_code, 409, delete_response.text)
        self.assertEqual(len(self.client.get("/api/providers").json()["providers"]), 1)

    def test_provider_not_found(self) -> None:
        self.assertEqual(self.client.post("/api/providers/nope/test").status_code, 404)
        self.assertEqual(self.client.get("/api/providers/nope/models").status_code, 404)
        self.assertEqual(self.client.delete("/api/providers/nope").status_code, 404)

    def test_detect_endpoint(self) -> None:
        response = self.client.post(
            "/api/providers/detect", json={"api_key": "sk-ant-abcdef123456"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["detected"])
        self.assertIn("not supported", body["hint"])
        self.assertNotIn("sk-ant-abcdef123456", json.dumps(body))
        # ambiguous sk- prefix returns candidates
        ambiguous = self.client.post(
            "/api/providers/detect", json={"api_key": "sk-abcdef1234567890"}
        ).json()
        self.assertTrue(ambiguous["detected"])
        self.assertGreaterEqual(len(ambiguous["candidates"]), 3)
        # unknown key → not detected, no error
        unknown = self.client.post(
            "/api/providers/detect", json={"api_key": "totally-unknown-key-value"}
        ).json()
        self.assertFalse(unknown["detected"])

    def test_presets_endpoint(self) -> None:
        response = self.client.get("/api/providers/presets")
        self.assertEqual(response.status_code, 200)
        presets = response.json()["presets"]
        self.assertGreaterEqual(len(presets), 10)
        self.assertNotIn("api_key", json.dumps(presets))
        self.assertNotIn("secret", json.dumps(presets).lower())
        self.assertTrue(all(p["type"] in {"openai-compatible", "local"} for p in presets))

    def test_rating_roundtrip_via_api(self) -> None:
        response = self.client.post(
            "/api/ratings",
            json={
                "agent_id": "kimi",
                "episode_id": self.episode["episode_id"],
                "dimensions": {
                    "requirement_fit": 5,
                    "correctness": 4,
                    "efficiency": 5,
                    "autonomy": 3,
                    "maintainability": 4,
                },
                "overall_preference": 4,
                "would_use_again": "maybe",
                "comment": "good work",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        rating = response.json()["rating"]
        self.assertTrue(rating["rating_id"].startswith("r-"))
        self.assertIsNotNone(response.json()["ledger_seq"])

        listed = self.client.get("/api/ratings?agent_id=kimi").json()["ratings"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["comment"], "good work")
        self.assertEqual(
            self.client.get("/api/ratings?agent_id=hermes").json()["ratings"], []
        )

    def test_rating_validation_via_api(self) -> None:
        response = self.client.post(
            "/api/ratings",
            json={
                "agent_id": "kimi",
                "episode_id": self.episode["episode_id"],
                "dimensions": {"correctness": 9},
                "overall_preference": 1,
                "would_use_again": "yes",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_provider_model_agent_profile_query(self) -> None:
        response = self.client.get(
            "/api/agent-profile", params={"agent_id": "p-test/model-x"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agent_id"], "p-test/model-x")

    def test_agent_profile_exposes_read_only_decision_profile(self) -> None:
        profile = self.client.get("/api/agents/kimi").json()
        decision = profile["decision_profile"]
        self.assertEqual(decision["schema_version"], "icle-agent-decision-profile/v0.2")
        self.assertEqual(decision["agent_id"], "kimi")
        self.assertTrue(decision["capabilities"])
        coding = next(item for item in decision["capabilities"] if item["domain"] == "coding")
        self.assertIn("base_prior", coding)
        self.assertIn("local_experience", coding)
        self.assertIn("quality", coding)
        self.assertIn("cost", coding)
        self.assertIn("measurement", decision)
        self.assertEqual(decision["measurement"]["ranking"], False)
        self.assertNotIn("total_score", decision["measurement"])
        # The detail endpoint is a projection only; it cannot write files.
        self.assertFalse((Path(self.client.app.state.store) / "observations").exists())

    def test_agent_profile_includes_user_rating(self) -> None:
        self.client.post(
            "/api/ratings",
            json={
                "agent_id": "kimi",
                "episode_id": self.episode["episode_id"],
                "dimensions": {
                    "requirement_fit": 5,
                    "correctness": 5,
                    "efficiency": 5,
                    "autonomy": 5,
                    "maintainability": 5,
                },
                "overall_preference": 5,
                "would_use_again": "yes",
            },
        )
        profile = self.client.get("/api/agents/kimi").json()
        self.assertEqual(profile["user_rating"]["count"], 1)
        self.assertEqual(profile["user_rating"]["dimensions"]["correctness"], 5.0)
        # capability numbers stay untouched by the rating
        self.assertIn("accepted", profile)


if __name__ == "__main__":
    unittest.main()
