"""Offline checks for the narrowly scoped, reviewed playlist update rules."""

from contextlib import redirect_stderr
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "playlist_updates.py"
SPEC = importlib.util.spec_from_file_location("playlist_updates", MODULE_PATH)
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)


def block(name="Example East HD", guide="Example.East.us", url=None):
    guide_attribute = f' tvg-id="{guide}"' if guide is not None else ""
    return [
        f'#EXTINF:-1{guide_attribute} tvg-name="Guide Name" '
        f'tvg-logo="https://example.invalid/logo.png" group-title="News",{name}',
        "#EXTGRP:Preserved group",
        "#EXTVLCOPT:http-referrer=https://example.invalid/old-player",
        "#KODIPROP:inputstream.adaptive.manifest_type=mpd",
        '#EXTHTTP:{"User-Agent":"Old Player"}',
        "# A comment that should survive a stream replacement",
        url or "https://example.invalid/old/master.m3u8",
    ]


def identity_rule(**overrides):
    rule = {
        "section": "live",
        "name": "Example East HD",
        "tvg_id": "Example.East.us",
    }
    rule.update(overrides)
    return rule


def replacement_rule(**overrides):
    return identity_rule(url="https://example.invalid/reviewed/master.m3u8", **overrides)


class ReviewedUpdatesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "reviewed.json"

    def load_rules(self, *, remove=(), replace=(), resolve=None):
        self.path.write_text(
            json.dumps({"remove": list(remove), "replace": list(replace)}),
            encoding="utf-8",
        )
        return updates.ReviewedUpdates(self.path, resolve=resolve)

    def test_exact_name_scope_ignores_case_and_outer_whitespace(self):
        reviewed = self.load_rules(
            remove=[identity_rule(name="  EXAMPLE east HD  ", reason="reviewed")]
        )

        self.assertIsNone(reviewed.apply("live", block("  example EAST hd  ")))

    def test_changed_section_or_guide_preserves_entry(self):
        reviewed = self.load_rules(remove=[identity_rule()])
        for section, guide in [
            ("Backup", "Example.East.us"),
            ("LIVE", "Example.East.us"),
            ("live", "Example.West.us"),
            ("live", "example.east.us"),
            ("live", None),
            ("live", ""),
        ]:
            with self.subTest(section=section, guide=guide):
                original = block(guide=guide)
                self.assertEqual(reviewed.apply(section, original), original)

    def test_shared_dummy_guide_does_not_remove_other_names_or_future_events(self):
        event_name = "Team A vs Team B - 2026-06-05 HD"
        reviewed = self.load_rules(
            remove=[
                identity_rule(name=event_name, tvg_id="Sport.Dummy.us"),
                identity_rule(name="Old Show", tvg_id="Blank.Dummy.us"),
                identity_rule(),
            ]
        )
        for name, guide in [
            ("Team A vs Team B - 2026-10-13 HD", "Sport.Dummy.us"),
            ("Team C vs Team D - 2026-06-05 HD", "Sport.Dummy.us"),
            ("Team A vs Team B - 2026-06-05 SD", "Sport.Dummy.us"),
            ("Another Show", "Blank.Dummy.us"),
            ("Example West HD", "Example.East.us"),
            ("Example East HD +1", "Example.East.us"),
        ]:
            with self.subTest(name=name):
                original = block(name, guide)
                self.assertEqual(reviewed.apply("live", original), original)

    def test_removal_still_applies_when_only_stream_url_changes(self):
        reviewed = self.load_rules(remove=[identity_rule()])
        original = block(url="https://example.invalid/new-provider/master.m3u8")
        before = copy.deepcopy(original)

        self.assertIsNone(reviewed.apply("live", original))
        self.assertEqual(original, before)

    def test_nonselected_block_is_unchanged(self):
        reviewed = self.load_rules(replace=[replacement_rule()])
        original = block(name="Unselected Feed")
        before = copy.deepcopy(original)

        self.assertEqual(reviewed.apply("live", original), before)
        self.assertEqual(original, before)

    def test_replacement_preserves_metadata_and_replaces_stream_options(self):
        new_options = [
            "#EXTVLCOPT:http-user-agent=New Player",
            '#EXTHTTP:{"Referer":"https://example.invalid/new-player"}',
        ]
        rule = replacement_rule(options=new_options)
        reviewed = self.load_rules(replace=[rule])
        original = block()
        before = copy.deepcopy(original)

        replaced = reviewed.apply("live", original)

        self.assertEqual(
            replaced,
            [before[0], before[1], before[5], *new_options, rule["url"]],
        )
        self.assertEqual(original, before)

    def test_invalid_or_login_credential_urls_are_rejected(self):
        for url in [
            "https://user:password@example.invalid/master.m3u8",
            "https://user@example.invalid/master.m3u8",
            "ftp://example.invalid/master.m3u8",
            "https:///master.m3u8",
            "/relative/master.m3u8",
        ]:
            with self.subTest(url=url):
                rule = identity_rule(url=url)
                with self.assertRaises(ValueError):
                    self.load_rules(replace=[rule])

    def test_duplicate_removal_rules_are_rejected_after_name_normalization(self):
        with self.assertRaises(ValueError):
            self.load_rules(
                remove=[identity_rule(), identity_rule(name=" EXAMPLE EAST HD ")]
            )

    def test_duplicate_replacement_rules_are_rejected_after_name_normalization(self):
        duplicate = replacement_rule(name=" example EAST hd ")
        duplicate["url"] = "https://example.invalid/different/master.m3u8"
        with self.assertRaises(ValueError):
            self.load_rules(replace=[replacement_rule(), duplicate])

    def test_conflicting_remove_and_replace_rules_are_rejected(self):
        with self.assertRaises(ValueError):
            self.load_rules(
                remove=[identity_rule(name=" EXAMPLE EAST HD ")],
                replace=[replacement_rule()],
            )

    def test_successful_refresh_uses_provider_url_and_passes_reviewed_rule(self):
        rule = replacement_rule(refresh={"provider": "example", "channel_id": "123"})
        calls = []

        def resolve(received, source_url):
            calls.append((copy.deepcopy(received), source_url))
            return "https://example.invalid/fresh/master.m3u8"

        reviewed = self.load_rules(replace=[rule], resolve=resolve)
        original = block()
        before = copy.deepcopy(original)
        errors = io.StringIO()
        with redirect_stderr(errors):
            replaced = reviewed.apply("live", original)

        self.assertEqual(calls, [(rule, original[-1])])
        self.assertEqual(replaced[-1], "https://example.invalid/fresh/master.m3u8")
        self.assertEqual(replaced[0], before[0])
        self.assertEqual(original, before)
        self.assertEqual(errors.getvalue(), "")

    def test_refresh_failure_uses_fallback_without_logging_error_secrets(self):
        rule = replacement_rule(refresh={"provider": "example"})
        secret_message = (
            "provider response https://example.invalid/private?token=TEST_SECRET "
            "password=TEST_PASSWORD"
        )

        def resolve(_rule, _source_url):
            raise RuntimeError(secret_message)

        reviewed = self.load_rules(replace=[rule], resolve=resolve)
        errors = io.StringIO()
        with redirect_stderr(errors):
            replaced = reviewed.apply("live", block())

        self.assertEqual(replaced[-1], rule["url"])
        self.assertIn("RuntimeError", errors.getvalue())
        self.assertIn("using reviewed fallback URL", errors.getvalue())
        for secret in [secret_message, "TEST_SECRET", "TEST_PASSWORD", "https://"]:
            self.assertNotIn(secret, errors.getvalue())

    def test_invalid_refreshed_url_uses_reviewed_fallback(self):
        rule = replacement_rule(refresh={"provider": "example"})
        reviewed = self.load_rules(
            replace=[rule],
            resolve=lambda _rule, _source_url: "https://user:TEST_PASSWORD@example.invalid/master.m3u8",
        )
        errors = io.StringIO()
        with redirect_stderr(errors):
            replaced = reviewed.apply("live", block())

        self.assertEqual(replaced[-1], rule["url"])
        self.assertIn("ValueError", errors.getvalue())
        self.assertNotIn("TEST_PASSWORD", errors.getvalue())

    def test_missing_resolver_preserves_reviewed_fallback(self):
        rule = replacement_rule(refresh={"provider": "example"})
        reviewed = self.load_rules(replace=[rule])
        errors = io.StringIO()
        with redirect_stderr(errors):
            replaced = reviewed.apply("live", block())

        self.assertEqual(replaced[-1], rule["url"])
        self.assertIn("using reviewed fallback URL", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
