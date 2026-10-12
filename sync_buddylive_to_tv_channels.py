#!/usr/bin/env python3
"""
Sync live (tv.m3u + Backup.m3u + TheTVApp.m3u8 + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex) into main.m3u.
- Fetches all nine remote playlists
- Keeps only channels whose display name does NOT start with '[' and is not Fanduel
- Skips Fanduel (by name or tvg-id), BBC America SD/HD, BET HD, and any stream URL containing moveonjoy
- Deduplicates by display name (first occurrence wins); later sources only add channels not already present
- Omits reviewed TheTVApp SD/HD duplicates when their live-section counterpart is present
- Writes main.m3u: live + Backup + TheTVApp + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex sections
"""
import argparse
import errno
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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

# Reviewed TheTVApp channel names mapped to their retained live display names.
# This is source-specific: preserve other variants, affiliates, and regional feeds.
# Bravo is intentionally excluded: TheTVApp is Eastern and live is Pacific.
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
    "nicktoons": "http://tvsen7.aynascope.net/nicktoons/index.m3u8",
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


def parse_m3u_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Parse M3U into (display_name, block_lines). Block = EXTINF + optional # lines + URL line."""
    lines = text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("#EXTINF"):
            last_comma = line.rfind(",")
            name = (line[last_comma + 1 :].strip() if last_comma >= 0 else "").strip()
            block = [line]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("http"):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                block.append(lines[i])
                i += 1
            blocks.append((name, block))
        else:
            i += 1
    return blocks


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
) -> str:
    max_attempts = max(1, max_attempts)
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync live into main.m3u")
    parser.add_argument(
        "--m3u",
        type=Path,
        default=Path("main.m3u"),
        help="Path to M3U file (e.g. main.m3u)",
    )
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
    ignore_names = {"fanduel", "bbc america sd", "bbc america hd", "bet hd"}

    fetch_kw = dict(
        http_timeout=args.http_timeout,
        http_retries=max(1, args.http_retries),
        retry_backoff_s=max(0.0, args.retry_backoff),
    )

    # Fetch and filter live (tv.m3u)
    try:
        live_blocks, live_skipped = fetch_and_filter(LIVE_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {LIVE_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Backup.m3u
    try:
        backup_blocks, backup_skipped = fetch_and_filter(BACKUP_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {BACKUP_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter TheTVApp.m3u8
    try:
        tvapp_blocks, tvapp_skipped = fetch_and_filter(THETVAPP_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {THETVAPP_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Xumo playlist
    try:
        xumo_blocks, xumo_skipped = fetch_and_filter(XUMO_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {XUMO_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter LocalNow playlist
    try:
        localnow_blocks, localnow_skipped = fetch_and_filter(
            LOCALNOW_URL, ignore_names, **fetch_kw
        )
    except Exception as e:
        print(f"Error fetching {LOCALNOW_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Tubi playlist
    try:
        tubi_blocks, tubi_skipped = fetch_and_filter(TUBI_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {TUBI_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Roku playlist
    try:
        roku_blocks, roku_skipped = fetch_and_filter(ROKU_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {ROKU_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Pluto TV playlist
    try:
        pluto_blocks, pluto_skipped = fetch_and_filter(PLUTO_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {PLUTO_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Plex playlist
    try:
        plex_blocks, plex_skipped = fetch_and_filter(PLEX_URL, ignore_names, **fetch_kw)
    except Exception as e:
        print(f"Error fetching {PLEX_URL}: {e}", file=sys.stderr)
        return 1

    # Deduplicate by display name (first occurrence wins)
    seen: set[str] = set()
    live_kept, seen, live_dup = dedupe_blocks_by_name(live_blocks, seen)
    backup_kept, seen, backup_dup = dedupe_blocks_by_name(backup_blocks, seen)
    tvapp_blocks, tvapp_live_dup = remove_thetvapp_live_duplicates(tvapp_blocks, live_kept)
    tvapp_kept, seen, tvapp_dup = dedupe_blocks_by_name(tvapp_blocks, seen)
    tvapp_dup += tvapp_live_dup
    xumo_kept, seen, xumo_dup = dedupe_blocks_by_name(xumo_blocks, seen)
    localnow_kept, seen, localnow_dup = dedupe_blocks_by_name(localnow_blocks, seen)
    tubi_kept, seen, tubi_dup = dedupe_blocks_by_name(tubi_blocks, seen)
    roku_kept, seen, roku_dup = dedupe_blocks_by_name(roku_blocks, seen)
    pluto_kept, seen, pluto_dup = dedupe_blocks_by_name(pluto_blocks, seen)
    plex_kept, seen, plex_dup = dedupe_blocks_by_name(plex_blocks, seen)

    # Build fresh M3U: header + live + Backup + TheTVApp + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex
    out_lines = [
        "#EXTM3U",
        f"# Last synced: {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z",
        "",
    ]
    out_lines.append(LIVE_SECTION)
    for block in live_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(BACKUP_SECTION)
    for block in backup_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(THETVAPP_SECTION)
    for block in tvapp_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(XUMO_SECTION)
    for block in xumo_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(LOCALNOW_SECTION)
    for block in localnow_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(TUBI_SECTION)
    for block in tubi_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(ROKU_SECTION)
    for block in roku_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(PLUTO_SECTION)
    for block in pluto_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(PLEX_SECTION)
    for block in plex_kept:
        out_lines.extend(block)
        out_lines.append("")
    out_text = "\n".join(out_lines)
    if not out_text.endswith("\n"):
        out_text += "\n"

    total = len(live_kept) + len(backup_kept) + len(tvapp_kept) + len(xumo_kept) + len(localnow_kept) + len(tubi_kept) + len(roku_kept) + len(pluto_kept) + len(plex_kept)
    if args.dry_run:
        print(f"Would write {total} channels to {m3u_path} (live: {len(live_kept)}, Backup: {len(backup_kept)}, TheTVApp: {len(tvapp_kept)}, Xumo: {len(xumo_kept)}, LocalNow: {len(localnow_kept)}, Tubi: {len(tubi_kept)}, Roku: {len(roku_kept)}, Pluto: {len(pluto_kept)}, Plex: {len(plex_kept)}; skipped fetch: live {live_skipped}, Backup {backup_skipped}, TheTVApp {tvapp_skipped}, Xumo {xumo_skipped}, LocalNow {localnow_skipped}, Tubi {tubi_skipped}, Roku {roku_skipped}, Pluto {pluto_skipped}, Plex {plex_skipped}; skipped dup: live {live_dup}, Backup {backup_dup}, TheTVApp {tvapp_dup}, Xumo {xumo_dup}, LocalNow {localnow_dup}, Tubi {tubi_dup}, Roku {roku_dup}, Pluto {pluto_dup}, Plex {plex_dup})")
        print(f"Output would be {len(out_lines)} lines")
        return 0

    m3u_path.write_text(out_text, encoding="utf-8")
    print(f"Synced {total} channels into {m3u_path} (live: {len(live_kept)}, Backup: {len(backup_kept)}, TheTVApp: {len(tvapp_kept)}, Xumo: {len(xumo_kept)}, LocalNow: {len(localnow_kept)}, Tubi: {len(tubi_kept)}, Roku: {len(roku_kept)}, Pluto: {len(pluto_kept)}, Plex: {len(plex_kept)}; skipped fetch: live {live_skipped}, Backup {backup_skipped}, TheTVApp {tvapp_skipped}, Xumo {xumo_skipped}, LocalNow {localnow_skipped}, Tubi {tubi_skipped}, Roku {roku_skipped}, Pluto {pluto_skipped}, Plex {plex_skipped}; skipped dup: live {live_dup}, Backup {backup_dup}, TheTVApp {tvapp_dup}, Xumo {xumo_dup}, LocalNow {localnow_dup}, Tubi {tubi_dup}, Roku {roku_dup}, Pluto {pluto_dup}, Plex {plex_dup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
