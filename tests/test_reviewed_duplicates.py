"""Offline contract checks for reviewed, feed-specific duplicate removal."""

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "sync_buddylive_to_tv_channels.py"
SPEC = importlib.util.spec_from_file_location("playlist_sync", MODULE_PATH)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def block(name, guide_id="Network.us", slug="sample"):
    guide = f' tvg-id="{guide_id}"' if guide_id is not None else ""
    return [
        f'#EXTINF:-1{guide} tvg-name="{name}" group-title="Test",{name}',
        "#EXTVLCOPT:http-user-agent=Test Player",
        "#KODIPROP:inputstream.adaptive.manifest_type=hls",
        f"https://example.invalid/{slug}/master.m3u8",
    ]


class ReviewedDuplicateBehaviorTests(unittest.TestCase):
    def setUp(self):
        rules = {
            sync.LIVE_SECTION: {
                "network (alt)": (sync.LIVE_SECTION, "network"),
            },
            sync.BACKUP_SECTION: {
                "network backup": (sync.LIVE_SECTION, "network"),
                "news 480p": (sync.BACKUP_SECTION, "news 720p"),
            },
            sync.THETVAPP_SECTION: {
                "network hd": (sync.LIVE_SECTION, "network"),
            },
        }
        self.rules = patch.object(sync, "REVIEWED_DUPLICATES", rules)
        self.aliases = patch.object(
            sync, "REVIEWED_GUIDE_ALIASES", {("network-east", "Network.us")}
        )
        self.rules.start()
        self.aliases.start()
        self.addCleanup(self.rules.stop)
        self.addCleanup(self.aliases.stop)

    def test_same_guide_removes_only_reviewed_name_in_reviewed_section(self):
        keeper = block("Network", slug="keeper")
        unrelated = block("Another Network", slug="other")
        same_name_elsewhere = block("Network Backup", slug="elsewhere")
        sections = {
            sync.LIVE_SECTION: [keeper, block("Network (Alt)")],
            sync.BACKUP_SECTION: [block("Network Backup"), unrelated],
            "# === Other ===": [same_name_elsewhere],
        }

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered[sync.LIVE_SECTION], [keeper])
        self.assertEqual(filtered[sync.BACKUP_SECTION], [unrelated])
        self.assertEqual(filtered["# === Other ==="], [same_name_elsewhere])
        self.assertEqual(removed.get(sync.LIVE_SECTION, 0), 1)
        self.assertEqual(removed.get(sync.BACKUP_SECTION, 0), 1)
        self.assertEqual(sum(removed.values()), 2)

    def test_absent_counterpart_preserves_candidate(self):
        sections = {sync.BACKUP_SECTION: [block("Network Backup")]}

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered, sections)
        self.assertEqual(sum(removed.values()), 0)

    def test_counterpart_in_wrong_section_does_not_authorize_removal(self):
        sections = {
            sync.BACKUP_SECTION: [block("Network")],
            sync.THETVAPP_SECTION: [block("Network HD")],
        }

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered, sections)
        self.assertEqual(sum(removed.values()), 0)

    def test_changed_or_missing_guide_ids_preserve_candidate(self):
        for duplicate_id, keeper_id in [
            ("Network.West.us", "Network.us"),
            ("Network.us", "Network.West.us"),
            (None, "Network.us"),
            ("Network.us", None),
            (None, None),
            ("", ""),
        ]:
            with self.subTest(duplicate_id=duplicate_id, keeper_id=keeper_id):
                sections = {
                    sync.LIVE_SECTION: [block("Network", keeper_id)],
                    sync.BACKUP_SECTION: [block("Network Backup", duplicate_id)],
                }

                filtered, removed = sync.remove_reviewed_duplicates(sections)

                self.assertEqual(filtered, sections)
                self.assertEqual(sum(removed.values()), 0)

    def test_explicit_guide_alias_is_accepted_only_in_reviewed_direction(self):
        sections = {
            sync.LIVE_SECTION: [block("Network", "Network.us")],
            sync.THETVAPP_SECTION: [block("Network HD", "network-east")],
        }

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered[sync.THETVAPP_SECTION], [])
        self.assertEqual(removed.get(sync.THETVAPP_SECTION, 0), 1)

        reverse = {
            sync.LIVE_SECTION: [block("Network", "network-east")],
            sync.THETVAPP_SECTION: [block("Network HD", "Network.us")],
        }
        filtered, removed = sync.remove_reviewed_duplicates(reverse)
        self.assertEqual(filtered, reverse)
        self.assertEqual(sum(removed.values()), 0)

    def test_later_counterpart_in_same_section_is_found(self):
        keeper = block("News 720p", "News.us", "news-keeper")
        unrelated = block("Other", "Other.us", "other")
        sections = {
            sync.BACKUP_SECTION: [
                block("News 480p", "News.us", "news-duplicate"),
                unrelated,
                keeper,
            ]
        }

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered[sync.BACKUP_SECTION], [unrelated, keeper])
        self.assertEqual(removed.get(sync.BACKUP_SECTION, 0), 1)

    def test_input_and_retained_metadata_options_and_order_are_unchanged(self):
        keeper = block("Network", slug="keeper")
        other = block("Other", "Other.us", "other")
        sections = {
            sync.LIVE_SECTION: [keeper, other],
            sync.BACKUP_SECTION: [block("Network Backup")],
        }
        before = copy.deepcopy(sections)

        filtered, _ = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(sections, before)
        self.assertEqual(list(filtered), list(sections))
        self.assertEqual(filtered[sync.LIVE_SECTION], before[sync.LIVE_SECTION])
        self.assertEqual(filtered[sync.BACKUP_SECTION], [])

    def test_removal_is_idempotent(self):
        sections = {
            sync.LIVE_SECTION: [block("Network"), block("Network (Alt)")],
            sync.BACKUP_SECTION: [block("Network Backup")],
        }

        first, first_removed = sync.remove_reviewed_duplicates(sections)
        second, second_removed = sync.remove_reviewed_duplicates(first)

        self.assertEqual(sum(first_removed.values()), 2)
        self.assertEqual(second, first)
        self.assertEqual(sum(second_removed.values()), 0)


class ReviewedDuplicateScopeTests(unittest.TestCase):
    def test_reviewed_rules_have_no_keeper_removal_chains(self):
        for section, rules in sync.REVIEWED_DUPLICATES.items():
            for duplicate_name, (keeper_section, keeper_name) in rules.items():
                with self.subTest(section=section, duplicate=duplicate_name):
                    self.assertEqual(duplicate_name, duplicate_name.lower())
                    self.assertEqual(keeper_name, keeper_name.lower())
                    self.assertNotIn(
                        keeper_name, sync.REVIEWED_DUPLICATES.get(keeper_section, {})
                    )

    def test_uncertain_and_distinct_feed_names_are_preserved(self):
        # Give every entry the same synthetic guide ID so only the reviewed
        # name/section scope can protect these deliberately excluded services.
        sections = {
            sync.LIVE_SECTION: [
                block("Bravo"),
                block("Real America's Voice"),
                block("Fox Deportes"),
            ],
            sync.TUBI_SECTION: [block("Fox Sports En Español")],
            sync.BACKUP_SECTION: [
                block("Bravo East"),
                block("Bravo Pacific"),
                block("Bravo Vault"),
                block("FOX DEPORTES USA"),
                block("America's Voice News"),
            ],
            sync.THETVAPP_SECTION: [block("Bravo SD"), block("Bravo HD")],
        }

        filtered, removed = sync.remove_reviewed_duplicates(sections)

        self.assertEqual(filtered, sections)
        self.assertEqual(sum(removed.values()), 0)


if __name__ == "__main__":
    unittest.main()
