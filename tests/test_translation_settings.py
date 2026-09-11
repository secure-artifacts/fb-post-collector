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
        db.setting_set("groq_api_key", "super-secret-key-value")
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"super-secret-key-value", response.data)
        self.assertIn("已配置；留空保持不变".encode(), response.data)

    def test_blank_key_keeps_existing_value_and_test_targets_selected_ai(self):
        db.setting_set("groq_api_key", "existing-groq-key")
        result = {"ok": True, "provider": "Groq", "text": "测试成功", "error": ""}
        with patch("fb_collector.app.test_translation_service", return_value=result) as test_service:
            response = self.client.post(
                "/settings",
                data={
                    "translation_provider": "groq",
                    "groq_api_key": "",
                    "groq_model": "openai/gpt-oss-20b",
                    "gemini_api_key": "",
                    "gemini_model": "gemini-3.8-flash",
                    "action": "test_translation",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.setting_get("groq_api_key"), "existing-groq-key")
        test_service.assert_called_once_with()
        self.assertIn("Groq 翻译测试成功".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
