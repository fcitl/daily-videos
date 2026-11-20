import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


class GeminiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "YOUTUBE_API_KEY": "youtube-test",
            "PEXELS_API_KEY": "pexels-test",
            "GEMINI_API_KEY": "gemini-test",
            "OUT_DIR": cls.directory.name,
        })
        cls.env.start()
        cls.edge = patch.dict(sys.modules, {"edge_tts": types.ModuleType("edge_tts")})
        cls.edge.start()
        cls.generator = importlib.import_module("generate_short")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("generate_short", None)
        cls.edge.stop()
        cls.env.stop()
        cls.directory.cleanup()

    def test_extracts_text_and_sends_key_in_header(self):
        response = Mock()
        response.json.return_value = {"candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "first "}, {"text": "second"}]},
        }]}
        with patch.object(self.generator.requests, "post", return_value=response) as post:
            self.assertEqual(self.generator._llm("prompt", 200), "first second")
            self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-test")
            self.assertEqual(post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"], "prompt")
            response.raise_for_status.assert_called_once()

    def test_rejects_blocked_or_incomplete_responses(self):
        for data in [
            {"promptFeedback": {"blockReason": "SAFETY"}},
            {"candidates": [{"finishReason": "MAX_TOKENS"}]},
            {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]},
        ]:
            with self.subTest(data=data):
                response = Mock()
                response.json.return_value = data
                with patch.object(self.generator.requests, "post", return_value=response):
                    with self.assertRaises(RuntimeError):
                        self.generator._llm("prompt")

    def test_surfaces_http_failures(self):
        response = Mock()
        response.raise_for_status.side_effect = self.generator.requests.HTTPError("HTTP 429")
        with patch.object(self.generator.requests, "post", return_value=response):
            with self.assertRaisesRegex(self.generator.requests.HTTPError, "429"):
                self.generator._llm("prompt")


if __name__ == "__main__":
    unittest.main()
