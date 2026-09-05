"""OpenAI-compatible client behaviour that local agents depend on."""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from icle.intelligence import IntelligenceError, OpenAICompatibleProvider  # noqa: E402


def _response(content: str = "ok"):
    payload = json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }).encode()
    response = mock.MagicMock()
    response.read.return_value = payload
    response.__enter__ = lambda self: self
    response.__exit__ = lambda *args: False
    return response


def _http_error(code: int, body: str):
    return urllib.error.HTTPError(
        "https://api.example.com/v1/chat/completions", code, "Bad Request", {},
        io.BytesIO(body.encode()),
    )


class TemperatureFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="k", model="m"
        )

    def _bodies(self, calls) -> list[dict]:
        return [json.loads(call.args[0].data.decode()) for call in calls]

    def test_temperature_zero_is_the_default(self):
        with mock.patch("icle.http_transport.open_authenticated", return_value=_response()) as urlopen:
            self.provider.complete("hi")
        self.assertEqual(self._bodies(urlopen.call_args_list)[0]["temperature"], 0)

    def test_a_pinned_temperature_is_dropped_and_retried(self):
        """Kimi's coding endpoint allows only temperature 1; determinism is
        preferred, not required, so the field is dropped rather than failing."""
        rejection = _http_error(
            400, '{"error":{"message":"invalid temperature: only 1 is allowed for this model"}}'
        )
        with mock.patch(
            "icle.http_transport.open_authenticated", side_effect=[rejection, _response("ok")]
        ) as urlopen:
            self.assertEqual(self.provider.complete("hi"), "ok")
        bodies = self._bodies(urlopen.call_args_list)
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["temperature"], 0)
        self.assertNotIn("temperature", bodies[1])

    def test_an_unrelated_400_is_not_retried(self):
        rejection = _http_error(400, '{"error":{"message":"model not found"}}')
        with mock.patch("icle.http_transport.open_authenticated", side_effect=rejection) as urlopen:
            with self.assertRaises(IntelligenceError):
                self.provider.complete("hi")
        self.assertEqual(urlopen.call_count, 1)

    def test_the_api_key_never_reaches_the_error_text(self):
        provider = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="super-secret", model="m"
        )
        with mock.patch("icle.http_transport.open_authenticated", side_effect=_http_error(500, "boom")):
            with self.assertRaises(IntelligenceError) as caught:
                provider.complete("hi")
        self.assertNotIn("super-secret", str(caught.exception))

    def test_a_retry_failure_is_still_sanitized(self):
        rejection = _http_error(400, '{"error":{"message":"invalid temperature"}}')
        provider = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="super-secret", model="m"
        )
        with mock.patch(
            "icle.http_transport.open_authenticated", side_effect=[rejection, _http_error(500, "boom")]
        ):
            with self.assertRaises(IntelligenceError) as caught:
                provider.complete("hi")
        self.assertNotIn("super-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
