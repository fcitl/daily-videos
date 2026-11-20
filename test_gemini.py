import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
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

    def test_pexels_auth_failure_is_not_treated_as_an_empty_search(self):
        response = Mock(ok=False, status_code=401, text="Unauthorized")
        with patch.object(self.generator.requests, "get", return_value=response) as get:
            with self.assertRaisesRegex(RuntimeError, "Pexels search failed \\(401\\)"):
                self.generator.fetch_broll(["car"])
            self.assertEqual(get.call_count, 1)

    def test_selects_relevant_video_and_rejects_bad_choices(self):
        videos = [
            {"snippet": {"title": "Blender balloon", "description": "Kitchen"}},
            {"snippet": {"title": "New car", "description": "A road car"}},
        ]
        with patch.object(self.generator, "_llm", return_value='{"index": 1}'):
            self.assertEqual(self.generator.select_vehicle_video(videos), videos[1])
        for answer in ['{"index": null}', '{"index": 9}', '{"index": true}', 'not json']:
            with self.subTest(answer=answer), patch.object(self.generator, "_llm", return_value=answer):
                with self.assertRaises(RuntimeError):
                    self.generator.select_vehicle_video(videos)

    def test_local_broll_requires_six_images_and_uses_sorted_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ["b.jpg", "a.png", "c.jpeg", "d.webp", "e.jpg"]:
                (path / name).touch()
            with self.assertRaisesRegex(RuntimeError, "at least 6"):
                self.generator.local_broll(path)
            (path / "f.JPG").touch()
            (path / "notes.txt").touch()
            images = self.generator.local_broll(path)
            self.assertEqual([p.name for p in images], [
                "a.png", "b.jpg", "c.jpeg", "d.webp", "e.jpg", "f.JPG",
            ])


if __name__ == "__main__":
    unittest.main()
