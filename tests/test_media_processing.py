import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

from fb_collector.services import ocr, translator
from fb_collector.services.facebook_graphql import (
    extract_single_post_fields,
    extract_story_id,
    extract_video_id,
    infer_post_id_from_url,
)
from fb_collector.services.scraper import FacebookScraper


class OcrTests(unittest.TestCase):
    def test_low_text_first_pass_uses_enhanced_second_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "small.png"
            Image.new("RGB", (200, 80), "white").save(image_path)
            tools = {
                "tesseract": {"available": True, "path": "tesseract"},
                "tessdata_dirs": [],
            }
            with patch.object(ocr, "detect_tools", return_value=tools), patch.object(
                ocr.pytesseract, "image_to_string", side_effect=["x", "clear readable words"]
            ) as recognize:
                result = ocr.ocr_image(image_path, "eng")
        self.assertEqual(result, "clear readable words")
        self.assertEqual(recognize.call_count, 2)
        self.assertIn("--psm 6", recognize.call_args_list[0].kwargs["config"])
        self.assertIn("--psm 11", recognize.call_args_list[1].kwargs["config"])

    def test_tessdata_path_with_spaces_uses_environment_not_quoted_argument(self):
        with tempfile.TemporaryDirectory(prefix="ocr path with spaces ") as directory:
            root = Path(directory)
            (root / "por.traineddata").write_bytes(b"test")
            image_path = root / "image.png"
            Image.new("RGB", (200, 80), "white").save(image_path)
            tools = {
                "tesseract": {"available": True, "path": "tesseract"},
                "tessdata_dirs": [str(root)],
            }

            def recognize(*args, **kwargs):
                self.assertEqual(os.environ.get("TESSDATA_PREFIX"), str(root))
                self.assertNotIn("tessdata-dir", kwargs["config"])
                return "texto reconhecido suficiente"

            with patch.object(ocr, "detect_tools", return_value=tools), patch.object(
                ocr.pytesseract, "image_to_string", side_effect=recognize
            ):
                self.assertEqual(ocr.ocr_image(image_path, "por"), "texto reconhecido suficiente")

    def test_enabled_ai_vision_ocr_is_preferred_over_tesseract(self):
        scraper = FacebookScraper()
        values = {"image_accessibility_text": ""}
        with patch("fb_collector.services.scraper.ocr_image") as local_ocr, patch(
            "fb_collector.services.scraper.ocr_image_with_ai",
            return_value={"text": "Texto visível", "error": "", "provider": "gemini"},
        ), patch("fb_collector.services.scraper.translate_field"):
            scraper.apply_ocr_to_local_media(Path("image.jpg"), {"ocr_languages": "por"}, values)
        self.assertEqual(values["ocr_text"], "Texto visível")
        self.assertEqual(values["ocr_status"], "ai_success")
        self.assertEqual(values["ocr_engine"], "gemini")
        local_ocr.assert_not_called()

    def test_video_frame_is_sent_to_ocr(self):
        scraper = FacebookScraper()
        values = {"local_video_path": "video.mp4", "media_url": "old"}
        frame = Path("frame.png")
        with patch("fb_collector.services.scraper.capture_video_frame", return_value=frame), patch(
            "fb_collector.services.scraper.upload_file", return_value="https://image.example/frame.png"
        ), patch.object(scraper, "apply_ocr_to_local_media") as apply_ocr:
            scraper.process_video_frame_media("https://video.example/a.mp4", {"ocr_languages": "por"}, values, [])
        apply_ocr.assert_called_once_with(frame, {"ocr_languages": "por"}, values)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        translator._translation_cache.clear()
        translator._last_request_at = 0.0
        translator._google_cooldown_until = 0.0
        translator._key_rotation = {"groq": 0, "gemini": 0}

    def test_api_key_parser_accepts_lines_commas_and_deduplicates(self):
        self.assertEqual(
            translator.parse_api_keys("first\nsecond, first;third"),
            ["first", "second", "third"],
        )

    def test_groq_key_pool_rotates_and_fails_over(self):
        limited = MagicMock()
        limited.status_code = 429
        limited.raise_for_status.side_effect = requests.HTTPError(response=limited)
        successful = MagicMock()
        successful.raise_for_status.return_value = None
        successful.json.return_value = {"choices": [{"message": {"content": "翻译成功"}}]}
        config = {
            "provider": "groq",
            "groq_api_keys": ["key-one", "key-two"],
            "groq_model": translator.DEFAULT_GROQ_MODEL,
            "gemini_api_keys": [],
            "gemini_model": translator.DEFAULT_GEMINI_MODEL,
        }
        with patch.object(translator.requests, "post", side_effect=[limited, successful]) as post, patch.object(
            translator.time, "sleep"
        ):
            self.assertEqual(translator.translate_chunk("first request", config), "翻译成功")
        authorizations = [call.kwargs["headers"]["Authorization"] for call in post.call_args_list]
        self.assertEqual(authorizations, ["Bearer key-one", "Bearer key-two"])

        successful_second_call = MagicMock()
        successful_second_call.raise_for_status.return_value = None
        successful_second_call.json.return_value = {"choices": [{"message": {"content": "第二次成功"}}]}
        with patch.object(translator.requests, "post", return_value=successful_second_call) as post, patch.object(
            translator.time, "sleep"
        ):
            self.assertEqual(translator.translate_chunk("second request", config), "第二次成功")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer key-two")

    def test_429_is_retried_then_cached(self):
        limited = MagicMock()
        limited.status_code = 429
        limited.raise_for_status.side_effect = requests.HTTPError(response=limited)
        successful = MagicMock()
        successful.status_code = 200
        successful.raise_for_status.return_value = None
        successful.json.return_value = [[['中文', 'texto', None, None]]]
        with patch.object(translator.requests, "get", side_effect=[limited, successful]) as get, patch.object(
            translator.time, "sleep"
        ):
            self.assertEqual(translator.translate_chunk("texto"), "中文")
            self.assertEqual(translator.translate_chunk("texto"), "中文")
        self.assertEqual(get.call_count, 2)

    def test_google_rate_limit_falls_back_to_mymemory(self):
        limited = MagicMock()
        limited.status_code = 429
        limited.raise_for_status.side_effect = requests.HTTPError(response=limited)
        fallback = MagicMock()
        fallback.status_code = 200
        fallback.raise_for_status.return_value = None
        fallback.json.return_value = {
            "responseData": {"translatedText": "音频翻译成功"},
            "responseStatus": 200,
        }
        with patch.object(translator.requests, "get", side_effect=[limited, limited, fallback]) as get, patch.object(
            translator.time, "sleep"
        ):
            self.assertEqual(translator.translate_chunk("audio translation"), "音频翻译成功")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[-1].args[0], translator.MYMEMORY_TRANSLATE_URL)

    def test_mymemory_chunks_respect_utf8_byte_limit(self):
        pieces = translator.split_utf8_bytes("斯瓦西里语和法语" * 100, 450)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece.encode("utf-8")) <= 450 for piece in pieces))

    def test_groq_translation_uses_configured_key(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "图片中的文字"}}]}
        config = {
            "provider": "groq",
            "groq_api_key": "groq-secret",
            "groq_model": "openai/gpt-oss-20b",
            "gemini_api_key": "",
            "gemini_model": translator.DEFAULT_GEMINI_MODEL,
        }
        with patch.object(translator.requests, "post", return_value=response) as post:
            self.assertEqual(translator.translate_chunk("text in image", config), "图片中的文字")
        self.assertEqual(post.call_args.args[0], translator.GROQ_TRANSLATE_URL)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer groq-secret")

    def test_gemini_translation_uses_header_key(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "音频中的文字"}]}}]
        }
        config = {
            "provider": "gemini",
            "groq_api_key": "",
            "groq_model": translator.DEFAULT_GROQ_MODEL,
            "gemini_api_key": "gemini-secret",
            "gemini_model": "gemini-3.8-flash",
        }
        with patch.object(translator.requests, "post", return_value=response) as post:
            self.assertEqual(translator.translate_chunk("audio text", config), "音频中的文字")
        self.assertIn("gemini-3.8-flash:generateContent", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-secret")

    def test_gemini_vision_ocr_sends_inline_image(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Texte dans l’image"}]}}]
        }
        with patch.object(translator.requests, "post", return_value=response) as post:
            result = translator.ocr_with_gemini("YWJj", "image/jpeg", "gemini-secret")
        self.assertEqual(result, "Texte dans l’image")
        inline = post.call_args.kwargs["json"]["contents"][0]["parts"][0]["inline_data"]
        self.assertEqual(inline, {"mime_type": "image/jpeg", "data": "YWJj"})


class GroupPostTargetingTests(unittest.TestCase):
    URL = "https://fb.com/groups/107571649019326/posts/1038161605960321"

    def test_group_url_wins_over_unrelated_ids_in_page_html(self):
        html = '<script>{"storyID":"wrong-story","videoID":"1801762634289117"}</script>'
        self.assertEqual(infer_post_id_from_url(self.URL), "1038161605960321")
        self.assertEqual(
            extract_story_id(self.URL, html),
            "UzpfSTEwNzU3MTY0OTAxOTMyNjpWSzoxMDM4MTYxNjA1OTYwMzIx",
        )
        self.assertEqual(extract_video_id(self.URL, html), "")

    def test_group_single_post_accepts_data_node_shape_and_keeps_url_id(self):
        payloads = [
            {
                "data": {
                    "node": {
                        "__typename": "Story",
                        "id": "opaque-story-node",
                        "post_id": "1038161605960321",
                        "comet_sections": {
                            "content": {
                                "story": {
                                    "actors": [{"id": "55", "name": "作者"}],
                                    "message": {"text": "群组贴文正文"},
                                }
                            }
                        },
                    }
                }
            }
        ]
        values = extract_single_post_fields(payloads, "1038161605960321")
        self.assertEqual(values["post_id"], "1038161605960321")
        self.assertEqual(values["author_name"], "作者")
        self.assertEqual(values["post_text"], "群组贴文正文")
        self.assertEqual(values["graphql_source"], "single_post")


if __name__ == "__main__":
    unittest.main()
