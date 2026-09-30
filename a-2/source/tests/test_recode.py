import csv
import tempfile
import unittest
from pathlib import Path

from protego import Protego

from robots_study.recode import classify, recode
from robots_study.validation import compare_csv


class RuleCases(unittest.TestCase):
    def classify_text(self, text, crawler="GPTBot"):
        return classify("example.test", text, crawler, Protego.parse(text))

    def test_inline_comment_does_not_hide_named_token(self):
        result = self.classify_text(
            "User-agent: Amazonbot # retained source comment\nDisallow: /", "Amazonbot"
        )
        self.assertTrue(result["named"])
        self.assertEqual(result["primary"], "full")

    def test_wildcard_full_block_is_not_explicit(self):
        result = self.classify_text("User-agent: *\nDisallow: /")
        self.assertFalse(result["named"])
        self.assertEqual(result["primary"], "full")

    def test_named_allow_takes_precedence_over_wildcard(self):
        result = self.classify_text("User-agent: *\nDisallow: /\nUser-agent: GPTBot\nAllow: /")
        self.assertEqual(result["primary"], "explicit")

    def test_wildcard_subpath_has_two_recorded_definitions(self):
        result = self.classify_text("User-agent: *\nDisallow: /login")
        self.assertEqual(result["primary"], "none")
        self.assertEqual(result["effect"], "partial")
        self.assertTrue(result["root_allowed"])

    def test_named_subpath_is_partial(self):
        result = self.classify_text("User-agent: GPTBot\nDisallow: /private/")
        self.assertEqual(result["primary"], "partial")

    def test_empty_200_and_missing_404_are_distinct(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            raw = base / "raw"
            raw.mkdir()
            (raw / "empty.test.txt").write_text("", encoding="utf-8")
            snapshot = base / "snapshot.csv"
            with snapshot.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["domain", "layer", "fetch_status"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"domain": "empty.test", "layer": "A", "fetch_status": "ok"},
                        {"domain": "missing.test", "layer": "A", "fetch_status": "not_found"},
                        {"domain": "failed.test", "layer": "A", "fetch_status": "ssl_error"},
                    ]
                )
            result = recode(snapshot, raw, base / "output")
            self.assertEqual(result["states"], {"present": 1, "absent": 1, "unreadable": 1})
            with (base / "output/robots_5category_long.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = {row["domain"]: row for row in csv.DictReader(handle)}
            self.assertEqual(rows["empty.test"]["cat5_primary"], "④무언급")
            self.assertEqual(rows["missing.test"]["cat5_primary"], "⑤파일부재")
            self.assertEqual(rows["failed.test"]["new_decision"], "NA")

    def test_reference_comparison_catches_single_changed_cell(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            original = base / "original.csv"
            changed = base / "changed.csv"
            original.write_text("domain,result\nexample.test,BLOCK\n", encoding="utf-8")
            changed.write_text("domain,result\nexample.test,ALLOW\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Saved-result mismatch"):
                compare_csv(changed, original)


if __name__ == "__main__":
    unittest.main()
