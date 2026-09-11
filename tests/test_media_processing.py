import tempfile
import unittest
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
