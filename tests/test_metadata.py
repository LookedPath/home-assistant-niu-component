"""Regression checks for metadata rejected by hassfest."""

import json
from pathlib import Path
import re
import unittest


NIU_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "niu"
TRANSLATION_KEY = re.compile(r"^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$")


class MetadataTest(unittest.TestCase):
    def test_manifest_excludes_hacs_only_metadata(self):
        manifest = json.loads((NIU_ROOT / "manifest.json").read_text())
        self.assertNotIn("hacs", manifest)

    def test_service_translation_keys_are_valid_and_english_matches(self):
        strings = json.loads((NIU_ROOT / "strings.json").read_text())["services"]
        english = json.loads((NIU_ROOT / "translations/en.json").read_text())["services"]
        self.assertEqual(strings, english)
        for service, definition in strings.items():
            self.assertRegex(service, TRANSLATION_KEY)
            for field in definition.get("fields", {}):
                self.assertRegex(field, TRANSLATION_KEY)
