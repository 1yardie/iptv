"""Provider refresh tests use synthetic responses and never make network calls."""

import importlib.util
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit


MODULE_PATH = Path(__file__).resolve().parents[1] / "provider_refresh.py"
SPEC = importlib.util.spec_from_file_location("provider_refresh", MODULE_PATH)
provider = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider)

CHANNEL_ID = "01234567-89AB-CDEF-0123-456789ABCDEF"
OTHER_ID = "FEDCBA98-7654-3210-FEDC-BA9876543210"
API_HOST = "test.api.cms.amdvids.com"


def stream_url(pairs=()):
    query = urlencode(pairs)
    return "https://media.example.invalid/master.m3u8" + ("?" + query if query else "")


def bootstrap(host=API_HOST):
    page = {
        "runtimeConfig": {
            "DSP_API_URL": host,
            "DSP_TOKEN": json.dumps({"token": "SYNTHETIC_PUBLIC_BOOTSTRAP_TOKEN"}),
        },
        "props": {
            "pageProps": {
                "page": {"channels": [{"_id": CHANNEL_ID, "slug": "example-news"}]}
            }
        },
    }
    return '<html><script id="__NEXT_DATA__">' + json.dumps(page) + "</script></html>"


def playback_response(**overrides):
    data = {
        "_id": CHANNEL_ID,
        "title": "Example News",
        "video_m3u8": stream_url([("ads.playbackid", CHANNEL_ID)]),
        "geocheck": True,
    }
    data.update(overrides)
    return data


class LocalNowResolverTests(unittest.TestCase):
    def resolver(self, data=None, host=API_HOST):
        calls = []
        data = playback_response() if data is None else data

        def fetch_text(url, headers=None):
            calls.append((url, headers))
            if url == provider.PUBLIC_PAGE:
                return bootstrap(host)
            if urlsplit(url).hostname != API_HOST:
                raise AssertionError("Unexpected API host requested")
            return json.dumps(data)

        return provider.LocalNowResolver(fetch_text), calls

    def test_bootstrap_is_lazy_and_cached_for_multiple_resolutions(self):
        resolver, calls = self.resolver()
        self.assertEqual(calls, [])

        first = resolver.resolve(CHANNEL_ID, "Example News")
        second = resolver.resolve(CHANNEL_ID, "Example News")

        self.assertEqual(sum(url == provider.PUBLIC_PAGE for url, _ in calls), 1)
        self.assertEqual(len(calls), 3)
        self.assertTrue(first["catalog_present"])
        self.assertEqual(first, second)
        self.assertEqual(first["provider_channel_id"], CHANNEL_ID)
        self.assertEqual(first["page"], provider.PUBLIC_PAGE + "/example-news")
        for url, headers in calls[1:]:
            self.assertEqual(urlsplit(url).hostname, API_HOST)
            self.assertEqual(headers["x-access-token"], "SYNTHETIC_PUBLIC_BOOTSTRAP_TOKEN")
            self.assertNotIn("x-forwarded-for", {key.lower() for key in headers})

    def test_untrusted_api_host_is_rejected_before_token_is_sent(self):
        for host in [
            "evil.example.invalid",
            "test.api.cms.amdvids.com.evil.example.invalid",
            "https://test.api.cms.amdvids.com",
            "test.api.cms.amdvids.com:443",
        ]:
            with self.subTest(host=host):
                resolver, calls = self.resolver(host=host)
                with self.assertRaises(ValueError):
                    resolver.resolve(CHANNEL_ID, "Example News")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], provider.PUBLIC_PAGE)

    def test_invalid_uuid_is_rejected_without_fetching(self):
        resolver, calls = self.resolver()
        for channel_id in ["not-a-uuid", CHANNEL_ID + "/other", "", "1234"]:
            with self.subTest(channel_id=channel_id), self.assertRaises(ValueError):
                resolver.resolve(channel_id, "Example News")
        self.assertEqual(calls, [])

    def test_mismatching_provider_uuid_or_title_is_rejected(self):
        for change in [
            {"_id": OTHER_ID},
            {"_id": ""},
            {"title": "Example News West"},
            {"title": "Different Channel"},
        ]:
            with self.subTest(change=change):
                resolver, _ = self.resolver(playback_response(**change))
                with self.assertRaises(ValueError):
                    resolver.resolve(CHANNEL_ID, "Example News")

    def test_restricted_or_protected_provider_response_is_rejected(self):
        for change in [
            {"locked": True},
            {"is_teaser": True},
            {"drm_config": {"system": "example-drm"}},
            {"geocheck": False},
        ]:
            with self.subTest(change=change):
                resolver, _ = self.resolver(playback_response(**change))
                with self.assertRaises(ValueError):
                    resolver.resolve(CHANNEL_ID, "Example News")

    def test_malformed_or_missing_media_response_is_rejected(self):
        for data in [[], "unexpected", playback_response(video_m3u8=None)]:
            with self.subTest(data=data):
                resolver, _ = self.resolver(data)
                with self.assertRaises(ValueError):
                    resolver.resolve(CHANNEL_ID, "Example News")

    def test_bootstrap_failure_is_cached_with_redacted_error_text(self):
        calls = []

        def fetch_text(url, headers=None):
            calls.append(url)
            raise RuntimeError("https://example.invalid/?token=SYNTHETIC_SECRET")

        resolver = provider.LocalNowResolver(fetch_text)
        for _ in range(2):
            with self.assertRaises(ValueError) as raised:
                resolver.resolve(CHANNEL_ID, "Example News")
            self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))
            self.assertNotIn("https://", str(raised.exception))
        self.assertEqual(calls, [provider.PUBLIC_PAGE])

    def test_title_matching_allows_epg_label_and_same_station_call_sign(self):
        self.assertTrue(provider.title_matches("Example News", "EPG | Example News"))
        self.assertTrue(provider.title_matches("WABC News New York", "WABC Local News"))
        self.assertFalse(provider.title_matches("WABC News New York", "WXYZ Local News"))
        self.assertFalse(provider.title_matches("Example News East", "Example News West"))


class ProviderUrlSanitizerTests(unittest.TestCase):
    def test_removes_direct_and_numbered_identifiers_but_keeps_routing(self):
        pairs = [
            ("ads.playbackid", CHANNEL_ID),
            ("ads.channel_id", "public-channel-123"),
            ("ads.channel_name", "Example_News"),
            ("ads.ip", "192.0.2.10"),
            ("device_id", "SYNTHETIC_DEVICE"),
            ("session", "SYNTHETIC_SESSION"),
            ("ads.n1", "ip"),
            ("ads.v1", "192.0.2.20"),
            ("ads.N2", "DEVICE_ID"),
            ("ads.V2", "SYNTHETIC_DEVICE_TWO"),
            ("ads.n3", "sessionid"),
            ("ads.v3", "SYNTHETIC_SESSION_THREE"),
        ]

        clean, removed = provider.sanitize_url(stream_url(pairs) + "#fragment", CHANNEL_ID)

        self.assertEqual(parse_qsl(urlsplit(clean).query), pairs[:3])
        self.assertEqual(set(removed), {key for key, _ in pairs[3:]})
        self.assertEqual(urlsplit(clean).fragment, "")

    def test_exact_numbered_public_routing_uuid_is_preserved(self):
        pairs = [("ads.N4", "playbackid"), ("ads.V4", CHANNEL_ID.lower())]

        clean, removed = provider.sanitize_url(stream_url(pairs), CHANNEL_ID)

        self.assertEqual(parse_qsl(urlsplit(clean).query), pairs)
        self.assertEqual(removed, [])

    def test_unexpected_or_unidentified_playback_id_is_rejected(self):
        for pairs, expected_id in [
            ([("ads.playbackid", OTHER_ID)], CHANNEL_ID),
            ([("ads.playbackid", CHANNEL_ID)], None),
            ([("ads.n1", "playbackid"), ("ads.v1", OTHER_ID)], CHANNEL_ID),
        ]:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                provider.sanitize_url(stream_url(pairs), expected_id)

    def test_auth_signature_and_expiry_fields_are_rejected(self):
        for pairs in [
            [("token", "SYNTHETIC_TOKEN")],
            [("ads.signature", "SYNTHETIC_SIGNATURE")],
            [("expires", "9999999999")],
            [("ads.N1", "ACCESS_TOKEN"), ("ads.V1", "SYNTHETIC_TOKEN")],
        ]:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                provider.sanitize_url(stream_url(pairs), CHANNEL_ID)

    def test_duplicate_keys_credentials_and_insecure_scheme_are_rejected(self):
        for url in [
            stream_url([("ads.ip", "192.0.2.1"), ("ADS.IP", "192.0.2.2")]),
            "https://user:password@media.example.invalid/master.m3u8",
            "http://media.example.invalid/master.m3u8",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                provider.sanitize_url(url, CHANNEL_ID)


class XumoLocalNowTests(unittest.TestCase):
    def source_url(self, extra=()):
        return provider.XUMO_ENDPOINT + "?" + urlencode(
            [("reqargs.apikey", "SYNTHETIC_PUBLIC_CLIENT_KEY"), *extra]
        )

    def test_normal_resolver_request_removes_ip_override(self):
        calls = []
        returned = stream_url([
            ("ads.channel_name", "DIST-Local_Now_Example"),
            ("ads.channel_id", "public-localnow-id"),
            ("ads.ip", "192.0.2.10"),
        ])

        def fetch_text(url, headers=None):
            calls.append((url, headers))
            return json.dumps(returned)

        result = provider.resolve_xumo_localnow(
            fetch_text,
            self.source_url([("REQARGS.CLIENTIP", "192.0.2.20")]),
        )

        self.assertEqual(len(calls), 1)
        params = dict(parse_qsl(urlsplit(calls[0][0]).query))
        self.assertEqual(params, {"reqargs.apikey": "SYNTHETIC_PUBLIC_CLIENT_KEY"})
        self.assertNotIn("x-forwarded-for", {key.lower() for key in calls[0][1]})
        self.assertEqual(result["provenance"], provider.XUMO_ENDPOINT)
        self.assertEqual(result["provider_channel_id"], "public-localnow-id")
        self.assertNotIn("ads.ip", dict(parse_qsl(urlsplit(result["url"]).query)))

    def test_wrong_endpoint_or_missing_client_key_is_rejected_before_fetch(self):
        endpoints = [
            "https://evil.example.invalid/api/v1/scte35stream/XumoV3",
            "http://ottcms.weathergroup.com/api/v1/scte35stream/XumoV3",
            "https://ottcms.weathergroup.com:443/api/v1/scte35stream/XumoV3",
            "https://user:password@ottcms.weathergroup.com/api/v1/scte35stream/XumoV3",
            "https://ottcms.weathergroup.com/api/v1/scte35stream/Other",
            provider.XUMO_ENDPOINT,
            provider.XUMO_ENDPOINT + "?reqargs.apikey=",
        ]

        def unexpected_fetch(*args, **kwargs):
            raise AssertionError("Invalid endpoint must not be requested")

        for source in endpoints:
            with self.subTest(source=source), self.assertRaises(ValueError):
                provider.resolve_xumo_localnow(unexpected_fetch, source)

    def test_source_zip_content_selector_is_preserved_while_client_ip_is_removed(self):
        requested = []

        def fetch_text(url, headers=None):
            requested.append(url)
            return json.dumps(stream_url([
                ("ads.channel_name", "DIST-Local_Now_Atlanta"),
                ("ads.channel_id", "public-atlanta-id"),
            ]))

        result = provider.resolve_xumo_localnow(
            fetch_text,
            self.source_url([
                ("reqargs.zipcode", "30303"),
                ("reqargs.clientip", "192.0.2.20"),
            ]),
        )

        self.assertEqual(
            dict(parse_qsl(urlsplit(requested[0]).query)),
            {
                "reqargs.apikey": "SYNTHETIC_PUBLIC_CLIENT_KEY",
                "reqargs.zipcode": "30303",
            },
        )
        self.assertEqual(result["regional_channel_name"], "DIST-Local_Now_Atlanta")

    def test_missing_local_now_identity_or_non_url_response_is_rejected(self):
        for response in [
            {"url": stream_url()},
            stream_url(),
            stream_url([("ads.channel_name", "DIST-Different_Channel")]),
        ]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                provider.resolve_xumo_localnow(
                    lambda url, headers=None: json.dumps(response), self.source_url()
                )


if __name__ == "__main__":
    unittest.main()
