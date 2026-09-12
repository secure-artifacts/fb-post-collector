import unittest
from unittest.mock import patch

from fb_collector import db
from fb_collector.app import create_app


class TranslationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_saved_api_key_is_not_rendered_back_to_browser(self):
        db.setting_set("groq_api_keys", "super-secret-key-value\nsecond-secret-key")
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"super-secret-key-value", response.data)
        self.assertNotIn(b"second-secret-key", response.data)
        self.assertIn("当前已配置 2 个".encode(), response.data)

    def test_new_keys_are_appended_deduplicated_and_test_targets_selected_ai(self):
        db.setting_set("groq_api_keys", "existing-groq-key")
        result = {"ok": True, "provider": "Groq", "text": "测试成功", "error": ""}
        with patch("fb_collector.app.test_translation_service", return_value=result) as test_service:
            response = self.client.post(
                "/settings",
                data={
                    "translation_provider": "groq",
                    "groq_api_keys_add": "existing-groq-key\nnew-groq-key",
                    "groq_model": "openai/gpt-oss-20b",
                    "gemini_api_keys_add": "",
                    "gemini_model": "gemini-3.8-flash",
                    "action": "test_translation",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.setting_get("groq_api_keys"), "existing-groq-key\nnew-groq-key")
        self.assertEqual(db.setting_get("groq_api_key"), "")
        test_service.assert_called_once_with()
        self.assertIn("Groq 翻译测试成功".encode(), response.data)

    def test_clear_removes_plural_and_legacy_keys(self):
        db.setting_set("gemini_api_keys", "first\nsecond")
        db.setting_set("gemini_api_key", "legacy")
        response = self.client.post(
            "/settings",
            data={
                "translation_provider": "gemini",
                "clear_gemini_api_keys": "1",
                "groq_model": "openai/gpt-oss-20b",
                "gemini_model": "gemini-3.8-flash",
                "action": "save_translation",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.setting_get("gemini_api_keys"), "")
        self.assertEqual(db.setting_get("gemini_api_key"), "")


if __name__ == "__main__":
    unittest.main()
