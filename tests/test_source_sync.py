import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import sync_buddylive_to_tv_channels as sync


def block(name, version='old', options=()):
    return [f'#EXTINF:-1 tvg-id="{name}",{name}', *options,
            f'https://streams.example/{name}/{version}.m3u8']


def fixture():
    return {f'# === {name} ===': [block(name)] for name in sync.SOURCE_SECTIONS.values()}


def fetcher(failures=()):
    def fetch(url):
        name = sync.SOURCE_SECTIONS[url]
        if name in failures:
            raise TimeoutError('https://private.example/?token=secret')
        return [block(name, 'fresh')], 0
    return fetch


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.previous = fixture()

    def refresh(self, fetch):
        with contextlib.redirect_stderr(io.StringIO()):
            return sync.refresh_sources(self.previous, fetch)

    def test_localnow_timeout_preserves_only_localnow_and_fetches_later_sources(self):
        self.previous[sync.LOCALNOW_SECTION][0].insert(1, '#EXTVLCOPT:http-referrer=https://example.test/')
        sections, report = self.refresh(fetcher({'LocalNow'}))
        self.assertEqual(sections[sync.LOCALNOW_SECTION], self.previous[sync.LOCALNOW_SECTION])
        for marker, blocks in sections.items():
            if marker != sync.LOCALNOW_SECTION:
                self.assertIn('/fresh.m3u8', blocks[0][-1])
        self.assertEqual([r['provider'] for r in report['providers'] if r['status'] == 'retained'], ['LocalNow'])
        self.assertNotIn('secret', json.dumps(report))

    def test_multiple_failures_are_independent(self):
        sections, report = self.refresh(fetcher({'live', 'LocalNow', 'Plex'}))
        for name in ('live', 'LocalNow', 'Plex'):
            marker = f'# === {name} ==='
            self.assertEqual(sections[marker], self.previous[marker])
        self.assertIn('/fresh.m3u8', sections[sync.ROKU_SECTION][0][-1])
        self.assertEqual(sum(r['status'] == 'updated' for r in report['providers']), 6)

    def test_all_failed_preserves_previous_entries(self):
        sections, report = self.refresh(fetcher(set(sync.SOURCE_SECTIONS.values())))
        self.assertEqual(sections, self.previous)
        sync.finish_summary(report, sync.compose_playlist(sections, 'now'))
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['publishable'])

    def test_unavailable_source_does_not_block_healthy_sources(self):
        self.previous.pop(sync.LOCALNOW_SECTION)
        sections, report = self.refresh(fetcher({'LocalNow'}))
        self.assertEqual(sections[sync.LOCALNOW_SECTION], [])
        sync.finish_summary(report, sync.compose_playlist(sections, 'now'))
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['total_channels'], 8)

    def test_empty_filtered_result_keeps_previous_source(self):
        sections, _ = self.refresh(lambda url: ([], 4) if url == sync.LOCALNOW_URL else fetcher()(url))
        self.assertEqual(sections[sync.LOCALNOW_SECTION], self.previous[sync.LOCALNOW_SECTION])

    def test_parser_round_trip_retains_options_and_section_identity(self):
        self.previous[sync.BACKUP_SECTION][0][1:1] = ['#KODIPROP:inputstream.adaptive.manifest_type=hls', '#EXTHTTP:{"User-Agent":"TV"}']
        text = sync.compose_playlist(self.previous, 'now')
        self.assertEqual(sync.previous_sections(text), self.previous)

    def test_duplicate_bare_url_in_backup_is_ignored(self):
        entry = block('Backup')
        text = '#EXTM3U\n' + '\n'.join(entry + [entry[-1]])
        self.assertEqual(sync.parse_m3u_blocks(text), [('Backup', entry)])

    def test_invalid_upstream_response_is_not_published(self):
        for text in ('<html>Unavailable</html>', '#EXTM3U\n',
                     '#EXTM3U\n#EXTINF:-1,Broken\n#EXTINF:-1,Next\nhttps://example.test/v',
                     '#EXTM3U\n#EXTINF:-1,Broken\n', '#EXTM3U\n#EXTINF:-1,Oops\nNot a URL'):
            with self.subTest(text=text), patch.object(sync, 'fetch_url_text', return_value=text):
                with self.assertRaises(sync.InvalidPlaylist):
                    sync.fetch_and_filter(sync.LOCALNOW_URL, set(), http_timeout=1, http_retries=1, retry_backoff_s=0)

    def test_malformed_response_uses_fallback_without_stopping_later_sources(self):
        def fetch(url):
            if url == sync.TUBI_URL:
                with patch.object(sync, 'fetch_url_text', return_value='<html>Error</html>'):
                    return sync.fetch_and_filter(url, set(), http_timeout=1, http_retries=1, retry_backoff_s=0)
            return fetcher()(url)
        sections, _ = self.refresh(fetch)
        self.assertEqual(sections[sync.TUBI_SECTION], self.previous[sync.TUBI_SECTION])
        self.assertIn('/fresh.m3u8', sections[sync.PLEX_SECTION][0][-1])

    def test_dedupe_order_and_reviewed_counterparts_are_preserved(self):
        def fetch(url):
            name = sync.SOURCE_SECTIONS[url]
            if name == 'live':
                return [block('NBC'), block('Animal Planet')], 0
            if name == 'TheTVApp':
                return [block('Animal Planet HD'), block('Extra')], 0
            return [block('NBC'), block(name)], 0
        sections, _ = self.refresh(fetch)
        self.assertEqual([sync._name_from_block(b) for b in sections[sync.THETVAPP_SECTION]], ['Extra'])
        all_names = [sync._name_from_block(b) for rows in sections.values() for b in rows]
        self.assertEqual(all_names.count('NBC'), 1)

    def test_http_errors_are_reported_without_url_or_response(self):
        err = HTTPError('https://example.test/?token=secret', 503, 'secret', None, None)
        self.assertEqual(sync.safe_error(err), 'HTTP 503')

    def test_unexpected_programming_errors_are_not_masked(self):
        def broken(url):
            raise TypeError('bug')
        with self.assertRaises(TypeError):
            self.refresh(broken)

    def test_pluto_failure_does_not_roll_back_other_sources(self):
        sections, report = self.refresh(fetcher())
        report['attempted_at'] = 'now'
        def failed(*args, **kwargs):
            raise sync.PlutoRefreshError('Pluto request returned HTTP 429')
        with contextlib.redirect_stderr(io.StringIO()):
            output = sync.renew_pluto_independently(sections, self.previous, sync.compose_playlist(self.previous, 'old'), report, renew=failed)
        parsed = sync.previous_sections(output)
        self.assertEqual(parsed[sync.PLUTO_SECTION], self.previous[sync.PLUTO_SECTION])
        self.assertIn('/fresh.m3u8', parsed[sync.PLEX_SECTION][0][-1])
        sync.finish_summary(report, output)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['pluto_renewal']['status'], 'retained')

    def test_partial_pluto_checks_are_visible(self):
        sections, report = self.refresh(fetcher())
        report['attempted_at'] = 'now'
        def partial(source, **kwargs):
            return source, {'failed_channels': ['abc'], 'updated_entries': 2}
        output = sync.renew_pluto_independently(sections, self.previous, '', report, renew=partial)
        sync.finish_summary(report, output)
        self.assertEqual(report['status'], 'partial')

    def test_cli_uses_explicit_previous_when_output_is_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous, target, summary = [Path(tmp) / name for name in ('main.m3u', 'output.m3u', 'report.json')]
            original = sync.compose_playlist(self.previous, 'old')
            previous.write_text(original)
            with patch('sys.argv', ['sync', '--previous', str(previous), '--m3u', str(target), '--summary', str(summary)]), \
                 patch.object(sync, 'fetch_and_filter', side_effect=lambda url, *a, **kw: fetcher({'LocalNow'})(url)), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(sync.main(), 0)
            self.assertEqual(previous.read_text(), original)
            parsed = sync.previous_sections(target.read_text())
            self.assertEqual(parsed[sync.LOCALNOW_SECTION], self.previous[sync.LOCALNOW_SECTION])
            self.assertIn('/fresh.m3u8', parsed[sync.PLEX_SECTION][0][-1])
            self.assertEqual(json.loads(summary.read_text())['status'], 'partial')
            self.assertIn('| LocalNow | retained |', summary.with_suffix('.md').read_text())

    def test_no_usable_channels_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'output.m3u'
            target.write_text('untouched')
            with patch('sys.argv', ['sync', '--previous', str(Path(tmp) / 'absent.m3u'), '--m3u', str(target)]), \
                 patch.object(sync, 'fetch_and_filter', side_effect=TimeoutError()), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(sync.main(), 1)
            self.assertEqual(target.read_text(), 'untouched')


if __name__ == '__main__':
    unittest.main()
