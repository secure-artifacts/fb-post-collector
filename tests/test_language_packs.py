import unittest

from fb_collector.services.component_installer import COMPONENT_LABELS, LANGUAGE_COMPONENTS, TESSDATA_URLS
from fb_collector.services.environment import CORE_TESSERACT_LANGS, OPTIONAL_TESSERACT_LANGS


class LanguagePackTests(unittest.TestCase):
    def test_optional_language_packs_are_separate_from_core_install(self):
        self.assertEqual(CORE_TESSERACT_LANGS, ("eng", "por", "ara", "chi_sim"))
        self.assertEqual(OPTIONAL_TESSERACT_LANGS, ("swa", "fra", "Latin"))
        self.assertEqual(
            LANGUAGE_COMPONENTS,
            {"tessdata_swa": "swa", "tessdata_fra": "fra", "tessdata_latin": "Latin"},
        )

    def test_optional_downloads_use_official_tesseract_repository(self):
        for language in ("swa", "fra", "Latin"):
            self.assertIn("github.com/tesseract-ocr/tessdata/", TESSDATA_URLS[language])
        self.assertIn("马达加斯加语", COMPONENT_LABELS["tessdata_latin"])


if __name__ == "__main__":
    unittest.main()
