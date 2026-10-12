"""Renew Pluto HLS URLs using an ordinary anonymous public-web session.

The default fetcher is intended to run on the publishing GitHub runner. Never
commit locally generated tokens: their claims include the caller's location.
Checks cover HLS manifests, not sustained playback or playback on another IP.
"""

import argparse
import base64
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


BOOT_URL = "https://boot.pluto.tv/v4/start"
WEB_URL = "https://pluto.tv/us/live-tv/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://pluto.tv",
    "Referer": "https://pluto.tv/",
}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MIN_TOKEN_SECONDS = 4 * 60 * 60  # Existing sync runs every three hours.
CHANNEL_ID = re.compile(r"[0-9a-f]{24}")


class PlutoRefreshError(ValueError):
    """A safe error message that contains no stream URLs or session claims."""


def _pluto_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and (host == "pluto.tv" or host.endswith(".pluto.tv"))
            and not parsed.username
            and not parsed.password
            and parsed.port is None
        )
    except ValueError:
        return False


class _PlutoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _pluto_url(newurl):
            raise PlutoRefreshError("Unexpected Pluto response redirect")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _fetch(url, headers=None):
    if not _pluto_url(url):
        raise PlutoRefreshError("Unexpected Pluto request destination")
    request = Request(url, headers=headers or HEADERS)
    with build_opener(_PlutoRedirects()).open(request, timeout=20) as response:
        chunks, size = [], 0
        deadline = time.monotonic() + 20
        while size <= MAX_RESPONSE_BYTES:
            if time.monotonic() > deadline:
                raise PlutoRefreshError("Pluto response exceeded the read-time limit")
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        data = b"".join(chunks)
    if len(data) > MAX_RESPONSE_BYTES:
        raise PlutoRefreshError("Pluto response exceeded the size limit")
    return data


def _read(fetch, url):
    if not _pluto_url(url):
        raise PlutoRefreshError("Unexpected Pluto request destination")
    try:
        body = fetch(url, headers=HEADERS)
        if isinstance(body, bytes):
            body = body.decode("utf-8-sig")
        if not isinstance(body, str) or len(body) > MAX_RESPONSE_BYTES:
            raise PlutoRefreshError("Unexpected Pluto response body")
        return body
    except HTTPError as exc:
        # HTTPError messages include the URL, which may contain a JWT.
        raise PlutoRefreshError(f"Pluto request returned HTTP {exc.code}") from None
    except PlutoRefreshError:
        raise
    except Exception as exc:
        raise PlutoRefreshError("Pluto request failed: " + type(exc).__name__) from None


class _VersionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.version = None
        self.next_data = []
        self.in_next_data = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") in ("appVersion", "app_version"):
            self.version = attrs.get("content")
        if tag == "script" and attrs.get("id") == "__NEXT_DATA__":
            self.in_next_data = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_next_data = False

    def handle_data(self, data):
        if self.in_next_data:
            self.next_data.append(data)


def _app_version(fetch, channel_id):
    parser = _VersionParser()
    parser.feed(_read(fetch, WEB_URL + channel_id))
    version = parser.version
    if not version and parser.next_data:
        try:
            version = json.loads("".join(parser.next_data))["props"]["globalAppVersion"]
        except (ValueError, KeyError, TypeError):
            pass
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9._-]{0,127}", version):
        raise PlutoRefreshError("Current Pluto web app version was not found")
    return version


def _boot(fetch, channel_id):
    version = _app_version(fetch, channel_id)
    params = {
        "appName": "web", "appVersion": version,
        "deviceVersion": "122.0.0", "deviceModel": "web",
        "deviceMake": "chrome", "deviceType": "web",
        "clientID": str(uuid4()), "clientModelNumber": "1.0.0",
        "channelSlug": channel_id,
    }
    # Do not override location, ad settings, or access restrictions.
    try:
        boot = json.loads(_read(fetch, BOOT_URL + "?" + urlencode(params)))
        if boot["session"]["activeRegion"] != "US":
            raise PlutoRefreshError("Pluto runner session is not in the US")
        server = boot["servers"]["stitcher"]
        server_parts = urlsplit(server)
        if not _pluto_url(server) or server_parts.path not in ("", "/") or server_parts.query or server_parts.fragment:
            raise PlutoRefreshError("Unexpected Pluto stitcher server")
        token = boot["sessionToken"]
        if not isinstance(token, str) or len(token) > 65536:
            raise PlutoRefreshError("Invalid Pluto session token")
        pieces = token.split(".")
        if len(pieces) != 3 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in pieces):
            raise PlutoRefreshError("Invalid Pluto session token")
        claims = json.loads(base64.urlsafe_b64decode(pieces[1] + "=" * (-len(pieces[1]) % 4)))
        expiry = claims["exp"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or expiry < time.time() + MIN_TOKEN_SECONDS:
            raise PlutoRefreshError("Pluto token has less than four hours remaining")
        if claims.get("activeRegion", "US") != "US":
            raise PlutoRefreshError("Pluto token region does not match the US session")
        stitcher_params = boot["stitcherParams"]
        if not isinstance(stitcher_params, str) or len(stitcher_params) > 65536:
            raise PlutoRefreshError("Invalid Pluto stitcher parameters")
        query = dict(parse_qsl(stitcher_params.lstrip("?"), keep_blank_values=True))
        query.update(jwt=token, masterJWTPassthrough="true", includeExtendedEvents="true")
        refresh = boot.get("refreshInSec")
        if isinstance(refresh, bool) or not isinstance(refresh, (int, float)) or refresh <= 0:
            refresh = None
        summary = {
            "expires_utc": datetime.fromtimestamp(expiry, timezone.utc).isoformat(),
            "region": "US", "app_version": version,
            "refresh_after_seconds": refresh,
        }
        return server.rstrip("/"), urlencode(query), summary
    except PlutoRefreshError:
        raise
    except Exception:
        raise PlutoRefreshError("Invalid Pluto bootstrap response") from None


def _channel_id(url):
    try:
        parsed = urlsplit(url.split("|", 1)[0])
        if parsed.hostname == "jmp2.uk":
            match = re.fullmatch(r"/plu-([0-9a-f]{24})\.m3u8", parsed.path, re.I)
        elif _pluto_url(url.split("|", 1)[0]):
            match = re.fullmatch(r"/(?:v\d+/)?stitch/(?:embed/)?hls/channel/([0-9a-f]{24})/(?:master|playlist)\.m3u8", parsed.path, re.I)
        else:
            match = None
        return match[1].lower() if match else None
    except ValueError:
        return None


def _entries(lines):
    entries, start = [], None
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            start = index
        elif line and not line.startswith("#"):
            channel_id = _channel_id(line)
            if channel_id:
                if start is None:
                    raise PlutoRefreshError("Pluto URL has no EXTINF entry")
                entries.append((channel_id, start, index))
            start = None
    return entries


def _check_hls(fetch, url, depth=0):
    if depth > 3:
        raise PlutoRefreshError("Pluto HLS nesting is too deep")
    body = _read(fetch, url).lstrip("\ufeff \t\r\n")
    if not body.startswith("#EXTM3U"):
        raise PlutoRefreshError("Pluto did not return an HLS playlist")
    if "ptv_takedownslates_all_" in unquote(body).lower():
        raise PlutoRefreshError("Pluto returned a known unavailable-device slate")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("#EXT-X-KEY:"):
            method = re.search(r"(?:[:,])METHOD=([^,]+)", line)
            if not method or method[1] not in ("NONE", "AES-128"):
                raise PlutoRefreshError("Protected Pluto HLS requires a player check")
    variants = []
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            following = next((v for v in lines[i + 1:] if not v.startswith("#")), None)
            if not following:
                raise PlutoRefreshError("Pluto HLS variant has no URL")
            bandwidth = re.search(r"(?:[:,])BANDWIDTH=(\d+)", line)
            variants.append((int(bandwidth[1]) if bandwidth else 0, urljoin(url, following)))
    if variants:
        failures = []
        for _, child in sorted(set(variants))[:3]:
            try:
                _check_hls(fetch, child, depth + 1)
                return
            except PlutoRefreshError as exc:
                failures.append(exc)
                if "HTTP 429" in str(exc):
                    raise
        raise PlutoRefreshError("No sampled Pluto HLS rendition passed")
    target = next((line for line in lines if line.startswith("#EXT-X-TARGETDURATION:")), "")
    if not re.fullmatch(r"#EXT-X-TARGETDURATION:[1-9][0-9]*", target):
        raise PlutoRefreshError("Pluto HLS media playlist has no target duration")
    for i, line in enumerate(lines[:-1]):
        if re.match(r"#EXTINF:[0-9]+(?:\.[0-9]+)?,", line):
            following = next((v for v in lines[i + 1:] if not v.startswith("#")), None)
            if following:
                segment = urlsplit(urljoin(url, following))
                if segment.scheme == "https" and segment.hostname and not segment.username and not segment.password:
                    return
    raise PlutoRefreshError("Pluto HLS playlist has no complete media segments")


def _previous_streams(text):
    if text is None:
        return {}
    if not text.lstrip("\ufeff \t\r\n").startswith("#EXTM3U"):
        raise PlutoRefreshError("Previous playlist is not an M3U playlist")
    lines = text.splitlines(keepends=True)
    previous = {}
    for channel_id, start, index in _entries(lines):
        raw_url = lines[index].strip()
        url = raw_url.split("|", 1)[0]
        if not _pluto_url(url):
            continue
        try:
            token = dict(parse_qsl(urlsplit(url).query))["jwt"]
            pieces = token.split(".")
            if len(pieces) != 3:
                continue
            claims = json.loads(base64.urlsafe_b64decode(pieces[1] + "=" * (-len(pieces[1]) % 4)))
            expiry = claims["exp"]
            if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or not expiry > time.time():
                continue
            options = [line for line in lines[start + 1:index] if _is_stream_option(line)]
            previous[channel_id] = (raw_url, options)
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    return previous


def _is_stream_option(line):
    return line.strip().startswith(("#EXTVLCOPT:", "#KODIPROP:", "#EXTHTTP:"))


def renew_playlist(text, *, fetch=None, limit=None, channel_ids=None, pilot=False, previous_text=None):
    """Return renewed text and a token-free summary; input is never written.

    fetch(url, headers=None) may return bytes or str. A schedule retains input
    entries whose HLS check fails, but aborts when none pass. A pilot includes
    only selected Pluto entries and requires every selected channel to pass.
    Failed scheduled entries may retain a previous unexpired Pluto URL and its
    stream options, while keeping the new entry's labels and other metadata.
    """
    if not text.lstrip("\ufeff \t\r\n").startswith("#EXTM3U"):
        raise PlutoRefreshError("Input is not an M3U playlist")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise PlutoRefreshError("Channel limit must be a positive integer")
    if channel_ids is not None and limit is not None:
        raise PlutoRefreshError("Specify channel IDs or a limit, not both")
    lines = text.splitlines(keepends=True)
    entries = _entries(lines)
    available = list(dict.fromkeys(row[0] for row in entries))
    if channel_ids is not None:
        selected = list(channel_ids)
        if not selected or any(not isinstance(cid, str) or not CHANNEL_ID.fullmatch(cid) for cid in selected):
            raise PlutoRefreshError("Invalid requested Pluto channel ID")
        if len(set(selected)) != len(selected):
            raise PlutoRefreshError("Duplicate requested Pluto channel IDs")
        if any(cid not in available for cid in selected):
            raise PlutoRefreshError("Requested Pluto channel is absent from the playlist")
        if pilot and any(sum(row[0] == cid for row in entries) != 1 for cid in selected):
            raise PlutoRefreshError("Pilot channel occurs more than once in the playlist")
    else:
        selected = available[:limit] if limit else available
    if not selected:
        raise PlutoRefreshError("No Pluto channels were found")
    previous = _previous_streams(previous_text) if not pilot else {}
    fetch = fetch or _fetch
    server, query, summary = _boot(fetch, selected[0])
    replacements, failed = {}, []
    for channel_id in selected:
        url = server + "/v2/stitch/hls/channel/" + channel_id + "/master.m3u8?" + query
        try:
            _check_hls(fetch, url)
            replacements[channel_id] = url
        except PlutoRefreshError as exc:
            failed.append(channel_id)
            if "HTTP 429" in str(exc):
                raise PlutoRefreshError("Pluto rate-limited renewal; batch not published") from None
            if pilot:
                raise PlutoRefreshError("Pilot HLS check failed for channel " + channel_id) from None
    if not replacements:
        raise PlutoRefreshError("No Pluto channels passed HLS validation")
    updated, retained = 0, 0
    fallback_blocks = {}
    for channel_id, start, index in entries:
        if channel_id in replacements:
            raw = lines[index]
            ending = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
            suffix = "|" + raw.strip().split("|", 1)[1] if "|" in raw else ""
            lines[index] = replacements[channel_id] + suffix + ending
            updated += 1
        elif channel_id in failed and channel_id in previous:
            raw = lines[index]
            ending = "\r\n" if raw.endswith("\r\n") else "\n"
            old_url, options = previous[channel_id]
            block = [line for line in lines[start:index] if not _is_stream_option(line)]
            block.extend(option.rstrip("\r\n") + ending for option in options)
            block.append(old_url + ending)
            fallback_blocks[start] = (index, block)
            retained += 1
    if pilot:
        header = next(line for line in lines if line.lstrip("\ufeff").startswith("#EXTM3U"))
        output = header.rstrip("\r\n") + "\n"
        for channel_id, start, index in entries:
            if channel_id in replacements:
                output += "".join(lines[start:index + 1]).rstrip("\r\n") + "\n"
    else:
        chunks, index = [], 0
        while index < len(lines):
            if index in fallback_blocks:
                end, block = fallback_blocks[index]
                chunks.extend(block)
                index = end + 1
            else:
                chunks.append(lines[index])
                index += 1
        output = "".join(chunks)
    summary.update(
        updated_entries=updated, checked_channels=len(selected),
        failed_channels=failed, available_channels=len(available),
        retained_previous_entries=retained,
        validation="master_and_media_playlist", pilot=pilot,
    )
    return output, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--previous")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--channel-id", action="append")
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    if args.channel_id and not args.pilot:
        parser.error("--channel-id requires --pilot")
    try:
        source = Path(args.input).read_bytes().decode("utf-8-sig")
        previous = Path(args.previous).read_bytes().decode("utf-8-sig") if args.previous else None
        output, summary = renew_playlist(source, limit=args.limit, channel_ids=args.channel_id, pilot=args.pilot, previous_text=previous)
        # Replace only after validation has completed; errors leave output intact.
        target = Path(args.output)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(output.encode("utf-8"))
        temporary.replace(target)
        if args.summary:
            Path(args.summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary))
    except PlutoRefreshError as exc:
        print("Pluto renewal failed: " + str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("Pluto renewal failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
