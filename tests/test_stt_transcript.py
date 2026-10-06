"""STT should ignore punctuation-only transcripts."""

import unittest

from app.openai.stt import is_actionable_transcript


class ActionableTranscriptTests(unittest.TestCase):
    def test_spoken_words(self):
        self.assertTrue(is_actionable_transcript("Hello"))
        self.assertTrue(is_actionable_transcript("haan"))
        self.assertTrue(is_actionable_transcript("हाँ"))

    def test_punctuation_and_empty(self):
        self.assertFalse(is_actionable_transcript(""))
        self.assertFalse(is_actionable_transcript("   "))
        self.assertFalse(is_actionable_transcript("।"))
        self.assertFalse(is_actionable_transcript("..."))
        self.assertFalse(is_actionable_transcript("?"))


if __name__ == "__main__":
    unittest.main()
