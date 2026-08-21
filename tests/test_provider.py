"""UI-16 ProviderConfig core tests: CRUD, secret masking, test/discover."""
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

from icle.provider import (  # noqa: E402
    PROVIDER_ROLES,
    ProviderError,
    default_provider_config,
    delete_provider,
    detect_provider,
    discover_models,
    get_provider,
    list_providers,
    mask_provider,
    resolve_secret,
    save_provider,
    test_and_discover,
    test_provider,
    validate_provider_config,
)

MODELS_PAYLOAD = json.dumps(
    {"data": [{"id": "kimi-k3", "display_name": "Kimi K3"}, {"id": "kimi-k2"}]}
).encode("utf-8")


class ModelsServer(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /models endpoint (Authorization must be present)."""

    def do_GET(self):  # noqa: N802
        if self.path != "/models":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") not in {"Bearer sk-test-key", "Bearer local"}:
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(MODELS_PAYLOAD)

    def log_message(self, *args):  # silence
        pass


class ProviderCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp())
        cls.store = cls.root / "store"
        cls.store.mkdir()
        server = HTTPServer(("127.0.0.1", 0), ModelsServer)
        cls.server = server
        cls.base_url = f"http://127.0.0.1:{server.server_port}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _config(self, provider_type: str = "openai-compatible", **overrides) -> dict:
        config = default_provider_config(
            display_name="Kimi", provider_type=provider_type, base_url=self.base_url,
            roles=["routing", "judging"], models=[],
        )
        config.update(overrides)
        return config

    def test_default_config_validates(self) -> None:
        config = self._config()
        validate_provider_config(config)
        self.assertEqual(config["status"], "misconfigured")
        self.assertEqual(config["roles"], ["routing", "judging"])

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(ProviderError):
            validate_provider_config(self._config(provider_type="magic"))

    def test_unknown_role_rejected(self) -> None:
        with self.assertRaises(ProviderError):
            validate_provider_config(self._config(roles=["winning"]))

    def test_http_type_requires_https_or_localhost(self) -> None:
        with self.assertRaises(ProviderError):
            validate_provider_config(
                self._config(base_url="http://evil.example.com")
            )

    def test_secret_ref_must_be_store_or_env(self) -> None:
        with self.assertRaises(ProviderError):
            validate_provider_config(self._config(secret_ref="plain:key"))

    def test_crud_and_masking(self) -> None:
        config = self._config()
        masked = save_provider(self.store, config, api_key="sk-secret-abc")
        self.assertEqual(masked["configured"], True)
        self.assertEqual(masked["secret_ref"], "store")
        self.assertNotIn("sk-secret-abc", json.dumps(masked))

        providers = list_providers(self.store)
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["provider_id"], config["provider_id"])
        self.assertNotIn("sk-secret-abc", json.dumps(providers))

        # raw secret only reachable through the internal resolver
        self.assertEqual(resolve_secret(self.store, config["provider_id"]), "sk-secret-abc")

        raw = get_provider(self.store, config["provider_id"])
        self.assertEqual(raw["display_name"], "Kimi")
        save_provider(self.store, {**raw, "display_name": "Kimi K3"})
        self.assertEqual(get_provider(self.store, config["provider_id"])["display_name"], "Kimi K3")

        delete_provider(self.store, config["provider_id"])
        self.assertEqual(list_providers(self.store), [])
        with self.assertRaises(ProviderError):
            get_provider(self.store, config["provider_id"])

    def test_env_secret_ref(self) -> None:
        import os

        config = self._config(secret_ref="env:ICLE_TEST_KEY")
        config["status"] = "connected"
        os.environ["ICLE_TEST_KEY"] = "env-secret"
        try:
            masked = save_provider(self.store, config)
            self.assertTrue(masked["configured"])
            self.assertEqual(masked["secret_ref"], "env:ICLE_TEST_KEY")
            self.assertEqual(resolve_secret(self.store, config["provider_id"]), "env-secret")
        finally:
            os.environ.pop("ICLE_TEST_KEY", None)

    def test_test_provider_connected(self) -> None:
        config = self._config()
        save_provider(self.store, config, api_key="sk-test-key")
        result = test_provider(self.store, config["provider_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "connected")
        self.assertIn("latency_ms", result)
        self.assertIsNotNone(get_provider(self.store, config["provider_id"])["last_checked_at"])

    def test_test_provider_wrong_key_unavailable(self) -> None:
        config = self._config()
        save_provider(self.store, config, api_key="sk-wrong")
        result = test_provider(self.store, config["provider_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")

    def test_test_provider_missing_secret(self) -> None:
        config = self._config()
        save_provider(self.store, config)
        result = test_provider(self.store, config["provider_id"])
        self.assertFalse(result["ok"])
        self.assertIn("secret", result["error"])

    def test_test_provider_cli_type(self) -> None:
        config = self._config(provider_type="cli", base_url="definitely-not-a-real-cmd-xyz")
        save_provider(self.store, config, api_key="ignored")
        result = test_provider(self.store, config["provider_id"])
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_local_provider_uses_http_models_without_key(self) -> None:
        config = self._config(provider_type="local", base_url=self.base_url)
        saved = save_provider(self.store, config)
        self.assertTrue(saved["configured"])
        result = test_and_discover(self.store, config["provider_id"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["models_synced"])

    def test_test_and_discover_syncs_models_when_empty(self) -> None:
        config = self._config()  # models=[] → auto-sync on successful test
        save_provider(self.store, config, api_key="sk-test-key")
        result = test_and_discover(self.store, config["provider_id"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["models_synced"])
        self.assertEqual(
            [m["id"] for m in result["provider"]["models"]], ["kimi-k3", "kimi-k2"]
        )
        # persisted on disk too
        saved = get_provider(self.store, config["provider_id"])
        self.assertEqual(len(saved["models"]), 2)

    def test_test_and_discover_keeps_manual_whitelist(self) -> None:
        config = self._config(models=[{"id": "manual-only"}])
        save_provider(self.store, config, api_key="sk-test-key")
        result = test_and_discover(self.store, config["provider_id"])
        self.assertTrue(result["ok"])
        # a manual list is never overwritten by discovery
        self.assertFalse(result["models_synced"])
        saved = get_provider(self.store, config["provider_id"])
        self.assertEqual([m["id"] for m in saved["models"]], ["manual-only"])

    def test_test_and_discover_failure_no_sync(self) -> None:
        config = self._config()
        save_provider(self.store, config, api_key="sk-wrong")
        result = test_and_discover(self.store, config["provider_id"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["models_synced"])
        self.assertEqual(get_provider(self.store, config["provider_id"])["models"], [])

    def test_discover_models(self) -> None:
        config = self._config()
        save_provider(self.store, config, api_key="sk-test-key")
        result = discover_models(self.store, config["provider_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [m["id"] for m in result["models"]], ["kimi-k3", "kimi-k2"]
        )
        self.assertEqual(result["models"][0]["alias"], "Kimi K3")

    def test_discover_models_unsupported_type(self) -> None:
        config = self._config(provider_type="custom", base_url="")
        save_provider(self.store, config)
        result = discover_models(self.store, config["provider_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["models"], [])

    def test_selected_model_must_be_in_models(self) -> None:
        config = self._config(models=[{"id": "v4-pro"}, {"id": "v4-flash"}])
        config["selected_model"] = "v4-pro"
        validate_provider_config(config)
        config["selected_model"] = "nope"
        with self.assertRaises(ProviderError):
            validate_provider_config(config)

    def test_selected_model_persists(self) -> None:
        config = self._config(models=[{"id": "v4-pro"}, {"id": "v4-flash"}])
        config["selected_model"] = "v4-flash"
        save_provider(self.store, config, api_key="sk-test-key")
        saved = get_provider(self.store, config["provider_id"])
        self.assertEqual(saved["selected_model"], "v4-flash")
        # switching is just a PATCH on the field
        save_provider(self.store, {**saved, "selected_model": "v4-pro"})
        self.assertEqual(
            get_provider(self.store, config["provider_id"])["selected_model"], "v4-pro"
        )

    def test_mask_never_leaks_secret(self) -> None:
        config = {**self._config(), "secret_ref": "store"}
        masked = mask_provider(config)
        self.assertEqual(masked["configured"], True)
        # the key VALUE must never appear; only the ref label and boolean remain
        self.assertNotIn("sk-", json.dumps(masked))
        self.assertNotIn("api_key", json.dumps(masked))
        self.assertEqual(PROVIDER_ROLES.count("routing"), 1)

    def test_detect_provider_by_key_prefix(self) -> None:
        cases = [
            ("sk-kimi-abcdef1234567890", "Kimi Code", "openai-compatible", "https://api.kimi.com/coding/v1"),
            ("sk-or-v1-abcdef123456", "OpenRouter", "openai-compatible", "https://openrouter.ai/api/v1"),
            ("gsk_abcdef123456", "Groq", "openai-compatible", "https://api.groq.com/openai/v1"),
            ("sk-abcdef1234567890", "OpenAI", "openai-compatible", "https://api.openai.com/v1"),
            ("AIzaSyabcdef1234567890", "Google Gemini", "openai-compatible", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            ("xai-abcdef1234567890", "xAI (Grok)", "openai-compatible", "https://api.x.ai/v1"),
            ("pplx-abcdef1234567890", "Perplexity", "openai-compatible", "https://api.perplexity.ai/v1"),
        ]
        for key, name, provider_type, base_url in cases:
            result = detect_provider(key)
            self.assertTrue(result["detected"], key)
            self.assertEqual(result["suggested"]["display_name"], name, key)
            self.assertEqual(result["suggested"]["type"], provider_type, key)
            self.assertEqual(result["suggested"]["base_url"], base_url, key)
        # Unsupported protocols are detected as unavailable, not offered as a fake connection.
        anthropic = detect_provider("sk-ant-abcdef123456")
        self.assertFalse(anthropic["detected"])
        self.assertIn("not supported", anthropic["hint"])
        # the raw key must never leak into the response
        for key, *_ in cases:
            self.assertNotIn(key, json.dumps(detect_provider(key)))

    def test_detect_ambiguous_sk_offers_candidates(self) -> None:
        result = detect_provider("sk-abcdef1234567890")
        names = [c["display_name"] for c in result["candidates"]]
        self.assertIn("Kimi Open Platform (CN)", names)
        self.assertIn("Kimi Open Platform (intl)", names)
        self.assertIn("DeepSeek", names)
        self.assertNotIn("Kimi Code", names)
        kimi_code = detect_provider("sk-kimi-abcdef1234567890")
        self.assertEqual(kimi_code["suggested"]["display_name"], "Kimi Code")
        self.assertEqual(kimi_code["suggested"]["base_url"], "https://api.kimi.com/coding/v1")
        self.assertNotEqual(kimi_code["suggested"]["base_url"], "https://api.openai.com/v1")
        self.assertNotIn(
            "https://api.moonshot.cn/v1",
            [item["base_url"] for item in kimi_code["candidates"]],
        )
        self.assertTrue(kimi_code["warning"])

    def test_detect_unknown_or_short_key(self) -> None:
        self.assertFalse(detect_provider("")["detected"])
        self.assertFalse(detect_provider("short")["detected"])
        self.assertFalse(detect_provider("zzz-not-a-real-key-prefix")["detected"])

    def test_presets_are_metadata_only(self) -> None:
        from icle.provider import PROVIDER_PRESETS, list_presets

        presets = list_presets()
        self.assertLess(len(presets), len(PROVIDER_PRESETS))
        self.assertGreaterEqual(len(presets), 28)
        self.assertTrue(all(p["type"] in {"openai-compatible", "local"} for p in presets))
        ids = {p["id"] for p in presets}
        # mainstream international + Chinese providers all covered
        for expected in ("openai", "google", "mistral", "xai", "cohere",
                         "perplexity", "openrouter", "groq", "kimi-code", "kimi", "kimi-intl",
                         "deepseek", "qwen", "qwen-intl", "qwen-coding",
                         "zhipu", "baichuan", "hunyuan", "minimax", "minimax-intl", "baidu", "ai360",
                         "lingyiwanwu", "stepfun", "siliconflow", "ollama", "lmstudio",
                         "vllm", "litellm"):
            self.assertIn(expected, ids, expected)
        by_id = {item["id"]: item for item in presets}
        self.assertEqual(by_id["kimi-code"]["base_url"], "https://api.kimi.com/coding/v1")
        self.assertEqual(by_id["kimi"]["base_url"], "https://api.moonshot.cn/v1")
        self.assertEqual(by_id["kimi-intl"]["base_url"], "https://api.moonshot.ai/v1")
        self.assertNotEqual(by_id["qwen"]["base_url"], by_id["qwen-coding"]["base_url"])
        self.assertNotEqual(by_id["minimax"]["base_url"], by_id["minimax-intl"]["base_url"])
        for preset in presets:
            self.assertNotIn("api_key", json.dumps(preset))
            self.assertNotIn("secret", json.dumps(preset).lower())
            self.assertIn("base_url", preset)
        # local presets don't require a key (LobeChat showApiKey: false)
        ollama = next(p for p in presets if p["id"] == "ollama")
        self.assertFalse(ollama.get("requires_key", True))


if __name__ == "__main__":
    unittest.main()
