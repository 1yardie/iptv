#!/usr/bin/env python3
"""
Sync live (tv.m3u + Backup.m3u + TheTVApp.m3u8 + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex) into main.m3u.
- Fetches all nine remote playlists
- Keeps only channels whose display name does NOT start with '[' and is not Fanduel
- Skips Fanduel (by name or tvg-id), BBC America SD/HD, BET HD, and any stream URL containing moveonjoy
- Deduplicates by display name (first occurrence wins); later sources only add channels not already present
- Omits reviewed TheTVApp SD/HD duplicates when their live-section counterpart is present
- Omits other reviewed duplicates only when a counterpart with matching guide metadata is retained
- Applies reviewed removals and replacements from playlist_updates.json before name deduplication
- Writes main.m3u: live + Backup + TheTVApp + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex sections
"""
import argparse
import errno
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

from playlist_updates import ReviewedUpdates
from pluto_refresh import PlutoRefreshError, renew_playlist
from provider_refresh import LocalNowResolver, resolve_xumo_localnow

LIVE_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/tv.m3u"
BACKUP_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/main/Backup/backup2.m3u8"
THETVAPP_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/TheTVApp.m3u8"
XUMO_URL = "https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/refs/heads/main/playlists/xumo_playlist.m3u"
LOCALNOW_URL = "https://www.apsattv.com/localnow.m3u"
TUBI_URL = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_all.m3u"
ROKU_URL = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/roku_all.m3u"
PLUTO_URL = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_us.m3u"
PLEX_URL = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_us.m3u"
LIVE_SECTION = "# === live ==="
BACKUP_SECTION = "# === Backup ==="
THETVAPP_SECTION = "# === TheTVApp ==="
XUMO_SECTION = "# === Xumo ==="
LOCALNOW_SECTION = "# === LocalNow ==="
TUBI_SECTION = "# === Tubi ==="
ROKU_SECTION = "# === Roku ==="
PLUTO_SECTION = "# === Pluto TV ==="
PLEX_SECTION = "# === Plex ==="

SOURCE_SECTIONS = {
    LIVE_URL: "live",
    BACKUP_URL: "Backup",
    THETVAPP_URL: "TheTVApp",
    XUMO_URL: "Xumo",
    LOCALNOW_URL: "LocalNow",
    TUBI_URL: "Tubi",
    ROKU_URL: "Roku",
    PLUTO_URL: "Pluto TV",
    PLEX_URL: "Plex",
}

# Reviewed TheTVApp channel names mapped to their retained live display names.
# This is source-specific: preserve other variants, affiliates, and regional feeds.
# Bravo is intentionally excluded because its East/Pacific feed metadata conflicts.
THETVAPP_LIVE_DUPLICATES = {
    "a&e": "a&e tv",
    "acc network": "acc network",
    "animal planet": "animal planet",
    "bbc world news": "bbc world news",
    "big ten network": "big ten network",
    "bloomberg tv": "bloomberg tv",
    "boomerang": "boomerang",
    "cbs sports network": "cbs sports network",
    "cmt": "cmt",
    "cnn": "cnn",
    "comedy central": "comedy central",
    "cooking channel": "cooking channel",
    "crime & investigation": "crime & investigation network",
    "destination america": "destination america",
    "e!": "e! entertainment",
    "espn2": "espn2",
    "espnews": "espnews",
    "espnu": "espnu",
    "fox news channel": "fox news",
    "fox sports 1": "fox sports 1",
    "fuse": "fuse",
    "fx movie": "fx movie channel",
    "fxx": "fxx",
    "fyi": "fyi tv",
    "golf channel": "golf channel",
    "hallmark": "hallmark channel",
    "history": "history channel",
    "investigation discovery": "investigation discovery",
    "lifetime": "lifetime",
    "lmn": "lifetime movie network",
    "mlb network": "mlb network",
    "mtv": "mtv",
    "nat geo wild": "nat geo wild",
    "national geographic": "nat geo",
    "newsmax tv": "newsmax tv",
    "nick jr": "nick jr",
    "outdoor channel": "outdoor channel",
    "own": "oprah winfrey network",
    "oxygen true crime": "oxygen true crime",
    "science": "science channel",
    "sec network": "sec network",
    "showtime 2": "showtime 2",
    "syfy": "syfy",
    "tbs": "tbs",
    "tcm": "turner classic movies",
    "the weather channel": "the weather channel",
    "tnt": "tnt",
    "travel channel": "travel channel",
    "trutv": "trutv",
    "tv one": "tv one",
    "usa network": "usa network",
    "vh1": "vh1",
    "vice": "vice tv",
}

# Reviewed on 2026-10-12: remove these failed/redundant entries only when the
# named counterpart survives the normal filters. Keys include the source section
# so a similarly named channel elsewhere cannot accidentally trigger a removal.
# FOX DEPORTES USA and America's Voice News remain: the suggested counterparts
# may be a different FAST service or regional affiliate, respectively.
REVIEWED_DUPLICATES = {
    LIVE_SECTION: {
        "disney channel (alt)": (LIVE_SECTION, "disney channel"),
        "history channel (alt)": (LIVE_SECTION, "history channel"),
        "nat geo (alt)": (LIVE_SECTION, "nat geo"),
        "nick jr (alt)": (LIVE_SECTION, "nick jr"),
    },
    BACKUP_SECTION: {
        "abc (wabc) new york": (LIVE_SECTION, "abc"),
        "abc (wabc) ny": (LIVE_SECTION, "abc"),
        "cbs (wcbs) new york": (LIVE_SECTION, "cbs"),
        "a&e": (LIVE_SECTION, "a&e tv"),
        "cbs sports golazo": (LIVE_SECTION, "cbs sports golazo network"),
        "cnn hd": (LIVE_SECTION, "cnn"),
        "e!": (LIVE_SECTION, "e! entertainment"),
        "espn 2": (LIVE_SECTION, "espn2"),
        "espn news": (LIVE_SECTION, "espnews"),
        "espn news hd": (LIVE_SECTION, "espnews"),
        "espn u": (LIVE_SECTION, "espnu"),
        "espn u hd": (LIVE_SECTION, "espnu"),
        "fs1 - fox sports 1": (LIVE_SECTION, "fox sports 1"),
        "fs1 - fox sports 1 hd": (LIVE_SECTION, "fox sports 1"),
        "fxm": (LIVE_SECTION, "fx movie channel"),
        "hallmark": (LIVE_SECTION, "hallmark channel"),
        "history": (LIVE_SECTION, "history channel"),
        "lifetime movies lmn": (LIVE_SECTION, "lifetime movie network"),
        "nfl network hd": (LIVE_SECTION, "nfl network"),
        "nfl network sd": (LIVE_SECTION, "nfl network"),
        "sky sports main event uk": (LIVE_SECTION, "sky sports main event"),
        "sky sports mix uk": (LIVE_SECTION, "sky sports mix"),
        "sky sports news uk": (LIVE_SECTION, "sky sports news"),
        "at&t sportsnet pittsburgh usa": (LIVE_SECTION, "sportsnet pittsburgh"),
        "sportsnet new york sny usa": (LIVE_SECTION, "sportsnet new york"),
        "sportsnet 360 ca": (LIVE_SECTION, "sportsnet 360"),
        "stv hd": (LIVE_SECTION, "stv"),
        "syfy hd": (LIVE_SECTION, "syfy"),
        "tbs hd": (LIVE_SECTION, "tbs"),
        "tbs east": (BACKUP_SECTION, "tbs east 720p (d)"),
        "tnt hd": (LIVE_SECTION, "tnt"),
        "tsn 3 ca": (LIVE_SECTION, "tsn 3"),
        "trutv east": (LIVE_SECTION, "trutv"),
        "vh1 hd": (LIVE_SECTION, "vh1"),
        "cnn 480p (d)": (BACKUP_SECTION, "cnn 720p (d)"),
        "livenow from fox": (LIVE_SECTION, "fox livenow"),
    },
    THETVAPP_SECTION: {
        "nicktoons sd": (LIVE_SECTION, "nicktoons"),
        "nicktoons hd": (LIVE_SECTION, "nicktoons"),
    },
}

# Exact reviewed guide aliases; a changed or missing guide ID keeps the entry
# for review.
REVIEWED_GUIDE_ALIASES = {
    ("plex.tv.CBS.Sports.Golazo.Network.plex", "CBS.Sports.Golazo.Network.us2"),
    ("FS1.Fox.Sports.1.HD.us2", "FS1.HD.us2"),
    ("63d025db4e83e700086eaa96", "plex.tv.LiveNOW.from.FOX.plex"),
    ("nicktoons-east", "Nicktoons.us2"),
}

# Preserve verified direct feeds when upstream playlists are refreshed.
STREAM_URL_OVERRIDES = {
    "alfred hitchcock presents": "https://xumo-xumoent-ch825-ku23q.fast.nbcuni.com/live/master.m3u8",
    "american crimes": "https://ch238-xumo-xumoent.fast.vsnt.net/live/master.m3u8",
    "american ninja warrior": "https://xumo-drct-ch812-n4996.fast.nbcuni.com/live/master.m3u8",
    "bravo vault": "https://xumo-xumoent-vc-114-x0p7i.fast.nbcuni.com/live/master.m3u8",
    "caso cerrado": "https://xumo-drct-ch836-57aiq.fast.nbcuni.com/live/master.m3u8",
    "dateline 24/7": "https://dz59iptwz5rxs.cloudfront.net/live/master.m3u8",
    "dateline en español": "https://ch831-xumo-drct-2dufi.fast.nbcuni.com/live/master.m3u8",
    "e! keeping up": "https://ch251-xumo-xumoent.fast.vsnt.net/live/master.m3u8",
    "el señor de los cielos": "https://xumo-drct-ch835-ekq0p.fast.nbcuni.com/live/master.m3u8",
    "golfpass": "https://ch253-xumo-xumoent.fast.vsnt.net/live/master.m3u8",
    "las vegas": "https://xumo-xumoent-vc-116-hrcw0.fast.nbcuni.com/live/master.m3u8",
    "lassie": "https://xumo-xumoent-ch833-p47sj.fast.nbcuni.com/live/master.m3u8",
    "law & order": "https://xumo-xumoent-ch837.fast.nbcuni.com/live/master.m3u8",
    "leave it to beaver": "https://xumo-xumoent-ch826-z0h0h.fast.nbcuni.com/live/master.m3u8",
    "little house on the prairie": "https://xumo-xumoent-vc-121-ghge7.fast.nbcuni.com/live/master.m3u8",
    "million dollar listing vault": "https://xumo-xumoent-ch838-coy7x.fast.nbcuni.com/live/master.m3u8",
    "murder; she wrote": "https://xumo-xumoent-vc-119-t7wx7.fast.nbcuni.com/live/master.m3u8",
    "nbc 10 boston news": "https://xumo-drct-ch111-af1nv.fast.nbcuni.com/live/master.m3u8",
    "nbc 10 philadelphia news": "https://xumo-drct-ch104-mnm32.fast.nbcuni.com/live/master.m3u8",
    "nbc 4 new york news": "https://xumo-drct-ch101-8hacu.fast.nbcuni.com/live/master.m3u8",
    "nbc 4 washington news": "https://xumo-drct-ch105-la3kc.fast.nbcuni.com/live/master.m3u8",
    "nbc 5 chicago news": "https://xumo-drct-ch107-91dzl.fast.nbcuni.com/live/master.m3u8",
    "nbc 5 dallas-fort worth news": "https://xumo-drct-ch106-9xq36.fast.nbcuni.com/live/master.m3u8",
    "nbc 6 south florida news": "https://xumo-drct-ch102-8fjca.fast.nbcuni.com/live/master.m3u8",
    "nbc bay area news": "https://xumo-drct-ch110-as8cj.fast.nbcuni.com/live/master.m3u8",
    "nbc comedy vault": "https://xumo-xumoent-vc-105-z0vpm.fast.nbcuni.com/live/master.m3u8",
    "nbc lx home": "https://xumo-drct-ch486-uqd91.fast.nbcuni.com/live/master.m3u8",
    "nicktoons": "http://41.205.93.154/NICKTOONS/index.m3u8",
    "noticias telemundo ahora": "https://xumo-xumoent-ch827-uj0ch.fast.nbcuni.com/live/master.m3u8",
    "oxygen true crime archives": "https://ch250-xumo-xumoent.fast.vsnt.net/live/master.m3u8",
    "real housewives vault": "https://xumo-xumoent-vc-123-8gq0q.fast.nbcuni.com/live/master.m3u8",
    "saved by the bell": "https://xumo-xumoent-vc-111-0pd1g.fast.nbcuni.com/live/master.m3u8",
    "telemundo deportes ahora": "https://xumo-drct-tda-8hacu.fast.nbcuni.com/live/master.m3u8",
    "telemundo noticias florida": "https://ch113-xumo-drct-hxz27.fast.nbcuni.com/live/master.m3u8",
    "telemundo noticias noreste": "https://ch115-xumo-drct-em2mv.fast.nbcuni.com/live/master.m3u8",
    "the lone ranger": "https://xumo-xumoent-ch829-wojlf.fast.nbcuni.com/live/master.m3u8",
    "the virginian": "https://ch815-comcast-xumo-afh3n.fast.nbcuni.com/live/master.m3u8",
    "universal action": "https://xumo-xumoent-ch828-w6val.fast.nbcuni.com/live/master.m3u8",
    "universal crime": "https://xumo-xumoent-vc-107-xmuvk.fast.nbcuni.com/live/master.m3u8",
    "universal monsters": "https://xumo-xumoent-ch824-d4q5c.fast.nbcuni.com/live/master.m3u8",
    "universal movies": "https://xumo-xumoent-ch839-d5ey0.fast.nbcuni.com/live/master.m3u8",
    "universal westerns": "https://xumo-xumoent-ch834-iwtka.fast.nbcuni.com/live/master.m3u8",
}


class InvalidPlaylist(ValueError):
    """A safe validation error, without response contents or stream URLs."""


def parse_m3u_blocks(text: str, *, allow_empty: bool = False) -> list[tuple[str, list[str]]]:
    """Validate entries before accepting a provider response or retained section."""
    lines = text.lstrip("\ufeff \t\r\n").splitlines()
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise InvalidPlaylist("Missing M3U header")
    blocks, block = [], None
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            if block is not None:
                raise InvalidPlaylist("Channel entry has no stream URL")
            if "," not in line or not line.rsplit(",", 1)[1].strip():
                raise InvalidPlaylist("Channel entry has no display name")
            block = [raw]
        elif line.startswith("#"):
            if block is not None:
                block.append(raw)
        else:
            parsed = urlsplit(line.split("|", 1)[0])
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise InvalidPlaylist("Invalid channel stream entry")
            if block is None:
                # Some sources contain a duplicate bare URL after a complete entry.
                # It has no channel metadata and the previous parser ignored it too.
                continue
            block.append(raw)
            blocks.append((block[0].rsplit(",", 1)[1].strip(), block))
            block = None
    if block is not None:
        raise InvalidPlaylist("Channel entry has no stream URL")
    if not blocks and not allow_empty:
        raise InvalidPlaylist("Playlist contains no channels")
    return blocks


def previous_sections(text: str) -> dict[str, list[list[str]]]:
    """Read only known provider sections; never attribute the whole file to one source."""
    if not text:
        return {}
    if not text.lstrip("\ufeff \t\r\n").startswith("#EXTM3U"):
        raise InvalidPlaylist("Previous playlist has no M3U header")
    chunks, current = {}, None
    markers = {f"# === {name} ===" for name in SOURCE_SECTIONS.values()}
    for line in text.splitlines():
        marker = line.strip()
        if marker.startswith("# === "):
            current = marker if marker in markers else None
            if current is not None:
                if current in chunks:
                    raise InvalidPlaylist("Previous playlist has duplicate source sections")
                chunks[current] = []
        elif current is not None:
            chunks[current].append(line)
    return {
        marker: [block for _, block in parse_m3u_blocks(
            "#EXTM3U\n" + "\n".join(lines), allow_empty=True)]
        for marker, lines in chunks.items()
    }


def safe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, InvalidPlaylist):
        return str(exc)
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
    ):
        return "Request timed out"
    # urllib and provider exceptions can contain token-bearing URLs.
    return type(exc).__name__


def compose_playlist(sections: dict[str, list[list[str]]], timestamp: str) -> str:
    lines = ["#EXTM3U", f"# Last sync attempted: {timestamp}", ""]
    for marker, blocks in sections.items():
        lines.append(marker)
        for block in blocks:
            lines.extend(block)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def refresh_sources(previous: dict, fetch) -> tuple[dict, dict]:
    """Fetch every provider, retaining only a failed provider's published entries."""
    sections, providers, seen = {}, [], set()
    for url, name in SOURCE_SECTIONS.items():
        marker = f"# === {name} ==="
        try:
            blocks, skipped = fetch(url)
            if not blocks:
                raise InvalidPlaylist("No channels remain after filtering")
            result = dict(provider=name, status="updated", filtered=skipped)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            blocks = previous.get(marker, [])
            result = dict(provider=name, status="retained" if marker in previous else "unavailable",
                          filtered=0, error=safe_error(exc))
            print(f"{name}: {result['status']} ({result['error']})", file=sys.stderr)
        duplicate_count = 0
        if marker == THETVAPP_SECTION:
            blocks, duplicate_count = remove_thetvapp_live_duplicates(blocks, sections[LIVE_SECTION])
        kept, seen, duplicates = dedupe_blocks_by_name(blocks, seen)
        sections[marker] = kept
        result.update(channels=len(kept), duplicates=duplicates + duplicate_count)
        providers.append(result)

    reviewed, removed = remove_reviewed_duplicates({
        marker: sections[marker] for marker in (LIVE_SECTION, BACKUP_SECTION, THETVAPP_SECTION)
    })
    sections.update(reviewed)
    for result in providers:
        marker = f"# === {result['provider']} ==="
        result["channels"] = len(sections[marker])
        result["duplicates"] += removed.get(marker, 0)
    return sections, dict(providers=providers)


def renew_pluto_independently(sections: dict, previous: dict, previous_text: str,
                             summary: dict, *, renew=renew_playlist) -> str:
    source = compose_playlist(sections, summary["attempted_at"])
    try:
        output, result = renew(source, previous_text=previous_text)
        result["status"] = "partial" if result["failed_channels"] else "updated"
    except PlutoRefreshError as exc:
        # A Pluto bootstrap/rate-limit failure must not discard other providers.
        # Keep the previously published Pluto section and its playback options.
        if PLUTO_SECTION in previous:
            sections[PLUTO_SECTION] = previous[PLUTO_SECTION]
            for provider in summary["providers"]:
                if provider["provider"] == "Pluto TV":
                    provider["channels"] = len(previous[PLUTO_SECTION])
        output = compose_playlist(sections, summary["attempted_at"])
        result = dict(status="retained" if PLUTO_SECTION in previous else "unrenewed",
                      error=str(exc), retained_previous_entries=len(previous.get(PLUTO_SECTION, [])))
        print("Pluto renewal: " + result["status"] + " (" + str(exc) + ")", file=sys.stderr)
    summary["pluto_renewal"] = result
    return output


def finish_summary(summary: dict, output: str) -> None:
    summary["total_channels"] = len(parse_m3u_blocks(output, allow_empty=True))
    updated = sum(row["status"] == "updated" for row in summary["providers"])
    summary["status"] = "complete" if updated == len(summary["providers"]) else "partial" if updated else "failed"
    if summary.get("pluto_renewal", {}).get("status", "updated") != "updated":
        if summary["status"] == "complete":
            summary["status"] = "partial"
    summary["publishable"] = summary["total_channels"] > 0
    if not summary["publishable"]:
        summary["status"] = "failed"


def write_summary(path: Path, summary: dict) -> None:
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = ["### Provider refresh", "", f"Result: **{summary['status']}**. Channels: {summary['total_channels']}.",
             "", "| Provider | Source refresh | Channels | Details |", "| --- | --- | ---: | --- |"]
    for row in summary["providers"]:
        lines.append(f"| {row['provider']} | {row['status']} | {row['channels']} | {row.get('error', '')} |")
    if "pluto_renewal" in summary:
        result = summary["pluto_renewal"]
        lines += ["", "### Pluto playback renewal", "", f"Result: **{result['status']}**.",
                  f"Updated entries: {result.get('updated_entries', 0)}. "
                  f"Failed checks: {len(result.get('failed_channels', []))}. "
                  f"Retained previous entries: {result.get('retained_previous_entries', 0)}."]
        if result.get("error"):
            lines.append(result["error"])
    lines += ["", "Retained sources were not refreshed; their URLs may still be expired. "
              "Successful sources are published before the workflow flags an incomplete refresh.",
              "Playlist refreshes and manifest checks do not confirm sustained TV playback."]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _transient_network_error(exc: BaseException) -> bool:
    """True if retrying the HTTP fetch might succeed (timeouts, overloaded origin, etc.)."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        r = exc.reason
        if isinstance(r, TimeoutError):
            return True
        if isinstance(r, OSError) and r.errno in (
            errno.ETIMEDOUT,
            errno.EPIPE,
            errno.ECONNRESET,
            errno.ECONNREFUSED,
        ):
            return True
        msg = str(r).lower()
        if "timed out" in msg or "timeout" in msg:
            return True
    return False


def fetch_url_text(
    url: str,
    *,
    timeout: float,
    max_attempts: int,
    retry_backoff_s: float,
    headers: dict[str, str] | None = None,
) -> str:
    max_attempts = max(1, max_attempts)
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < max_attempts - 1 and _transient_network_error(e):
                time.sleep(retry_backoff_s * (attempt + 1))
                continue
            raise


def should_skip(name: str, ignore_name_start: str, ignore_names: set[str]) -> bool:
    if not name:
        return True
    if ignore_name_start and name.lstrip().startswith(ignore_name_start):
        return True
    if name.strip().lower() in ignore_names:
        return True
    return False


def fetch_and_filter(
    url: str,
    ignore_names: set[str],
    *,
    http_timeout: float,
    http_retries: int,
    retry_backoff_s: float,
    updates: ReviewedUpdates | None = None,
) -> tuple[list[list[str]], int]:
    """Fetch M3U from url, filter (no '[', no Fanduel). Return (list of channel blocks, skipped count)."""
    text = fetch_url_text(
        url,
        timeout=http_timeout,
        max_attempts=http_retries,
        retry_backoff_s=retry_backoff_s,
    )
    blocks = parse_m3u_blocks(text)
    kept = []
    skipped = 0
    for name, block in blocks:
        if should_skip(name, "[", ignore_names) or "fanduel" in name.lower():
            skipped += 1
            continue
        # Also skip if EXTINF line has Fanduel in tvg-id/tvg-name (e.g. FDSN, FanDuel.TV.us)
        extinf_line = block[0] if block else ""
        if "fanduel" in extinf_line.lower():
            skipped += 1
            continue
        # Skip if stream URL contains moveonjoy
        block_text = "\n".join(block).lower()
        if "moveonjoy" in block_text:
            skipped += 1
            continue
        stream_url = STREAM_URL_OVERRIDES.get(name.lower())
        if stream_url:
            block[-1] = stream_url
        if updates is not None:
            block = updates.apply(SOURCE_SECTIONS[url], block)
            if block is None:
                skipped += 1
                continue
        kept.append(block)
    return kept, skipped


def _name_from_block(block: list[str]) -> str:
    """Extract display name from first EXTINF line (text after last comma)."""
    if not block:
        return ""
    extinf = block[0]
    last_comma = extinf.rfind(",")
    return (extinf[last_comma + 1 :].strip() if last_comma >= 0 else "").strip()


def dedupe_blocks_by_name(
    blocks: list[list[str]], seen: set[str]
) -> tuple[list[list[str]], set[str], int]:
    """Keep only blocks whose display name (lowercase) is not in seen. Return (kept, updated_seen, skipped_dup)."""
    kept = []
    skipped_dup = 0
    for block in blocks:
        name = _name_from_block(block)
        key = name.lower()
        if key in seen:
            skipped_dup += 1
            continue
        seen.add(key)
        kept.append(block)
    return kept, seen, skipped_dup


def remove_thetvapp_live_duplicates(
    blocks: list[list[str]], live_blocks: list[list[str]]
) -> tuple[list[list[str]], int]:
    """Drop reviewed TheTVApp duplicates only if their live counterpart was kept."""
    live_names = {_name_from_block(block).lower() for block in live_blocks}
    kept = []
    for block in blocks:
        name = _name_from_block(block).lower()
        base_name = re.sub(r"(?:\s+(?:hd|sd))+$", "", name).rstrip(".")
        if THETVAPP_LIVE_DUPLICATES.get(base_name) not in live_names:
            kept.append(block)
    return kept, len(blocks) - len(kept)


def remove_reviewed_duplicates(
    sections: dict[str, list[list[str]]],
) -> tuple[dict[str, list[list[str]]], dict[str, int]]:
    """Remove reviewed aliases only if their retained counterpart still matches."""
    by_name = {
        (section, _name_from_block(block).lower()): block
        for section, blocks in sections.items()
        for block in blocks
    }

    def guide_id(block: list[str]) -> str:
        match = re.search(r'\btvg-id="([^"]*)"', block[0])
        return match.group(1) if match else ""

    kept_sections = {}
    removed_counts = {}
    for section, blocks in sections.items():
        kept = []
        for block in blocks:
            counterpart_key = REVIEWED_DUPLICATES.get(section, {}).get(
                _name_from_block(block).lower()
            )
            counterpart = by_name.get(counterpart_key)
            if counterpart is not None:
                duplicate_id, retained_id = guide_id(block), guide_id(counterpart)
                if (duplicate_id and duplicate_id == retained_id) or (
                    duplicate_id, retained_id
                ) in REVIEWED_GUIDE_ALIASES:
                    continue
            kept.append(block)
        kept_sections[section] = kept
        removed_counts[section] = len(blocks) - len(kept)
    return kept_sections, removed_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync live into main.m3u")
    parser.add_argument(
        "--m3u",
        type=Path,
        default=Path("main.m3u"),
        help="Path to M3U file (e.g. main.m3u)",
    )
    parser.add_argument("--previous", type=Path, help="Last published playlist (defaults to --m3u)")
    parser.add_argument("--summary", type=Path, help="Write token-free JSON and Markdown refresh reports")
    parser.add_argument("--renew-pluto", action="store_true", help="Renew Pluto without blocking other sources")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done, do not write",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=90.0,
        metavar="SEC",
        help="Per-attempt socket timeout for each playlist URL (default: 90)",
    )
    parser.add_argument(
        "--http-retries",
        type=int,
        default=4,
        metavar="N",
        help="Max attempts per URL on transient errors (default: 4)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=3.0,
        metavar="SEC",
        help="Base seconds between retries; scaled by attempt (default: 3)",
    )
    args = parser.parse_args()

    m3u_path = args.m3u
    previous_path = args.previous or m3u_path
    previous_text = previous_path.read_text(encoding="utf-8-sig") if previous_path.exists() else ""
    previous = previous_sections(previous_text)
    ignore_names = {"fanduel", "bbc america sd", "bbc america hd", "bet hd"}

    def provider_fetch(url: str, headers=None) -> str:
        return fetch_url_text(
            url, timeout=min(30.0, args.http_timeout),
            max_attempts=min(2, max(1, args.http_retries)),
            retry_backoff_s=max(0.0, args.retry_backoff), headers=headers,
        )

    localnow = LocalNowResolver(provider_fetch)

    def resolve_update(rule: dict, source_url: str) -> str:
        refresh = rule["refresh"]
        if refresh["provider"] == "localnow":
            return localnow.resolve(refresh["channel_id"], rule["name"])["url"]
        if refresh["provider"] == "xumo_localnow":
            return resolve_xumo_localnow(provider_fetch, source_url)["url"]
        raise ValueError("Unknown refresh provider")

    updates = ReviewedUpdates(
        Path(__file__).with_name("playlist_updates.json"), resolve=resolve_update
    )
    fetch_kw = dict(
        http_timeout=args.http_timeout,
        http_retries=max(1, args.http_retries),
        retry_backoff_s=max(0.0, args.retry_backoff),
        updates=updates,
    )

    sections, summary = refresh_sources(
        previous, lambda url: fetch_and_filter(url, ignore_names, **fetch_kw)
    )
    summary["attempted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = compose_playlist(sections, summary["attempted_at"])
    if args.renew_pluto:
        output = renew_pluto_independently(sections, previous, previous_text, summary)
    finish_summary(summary, output)
    if args.summary:
        write_summary(args.summary, summary)
    if not summary["publishable"]:
        print("No usable channels; output was not replaced.", file=sys.stderr)
        return 1
    for row in summary["providers"]:
        print(f"{row['provider']}: {row['status']}, {row['channels']} channels")
    if args.dry_run:
        print(f"Would write {summary['total_channels']} channels ({summary['status']}) to {m3u_path}")
        return 0
    # An interrupted write must not leave a truncated published playlist.
    temporary = m3u_path.with_name(m3u_path.name + ".tmp")
    temporary.write_text(output, encoding="utf-8")
    temporary.replace(m3u_path)
    print(f"Synced {summary['total_channels']} channels ({summary['status']}) into {m3u_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
