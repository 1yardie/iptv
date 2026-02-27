#!/usr/bin/env python3
"""
Sync live (tv.m3u + Backup.m3u + TheTVApp.m3u8 + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex) into main.m3u.
- Fetches all nine remote playlists
- Keeps only channels whose display name does NOT start with '[' and is not Fanduel
- Skips Fanduel (by name or tvg-id), BBC America SD/HD, BET HD, and any stream URL containing moveonjoy
- Deduplicates by display name (first occurrence wins); later sources only add channels not already present
- Writes main.m3u: live + Backup + TheTVApp + Xumo + LocalNow + Tubi + Roku + Pluto TV + Plex sections
"""
import argparse
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LIVE_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/tv.m3u"
BACKUP_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/Backup.m3u"
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


def should_skip(name: str, ignore_name_start: str, ignore_names: set[str]) -> bool:
    if not name:
        return True
    if ignore_name_start and name.lstrip().startswith(ignore_name_start):
        return True
    if name.strip().lower() in ignore_names:
        return True
    return False


def fetch_and_filter(url: str, ignore_names: set[str]) -> tuple[list[list[str]], int]:
    """Fetch M3U from url, filter (no '[', no Fanduel). Return (list of channel blocks, skipped count)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")
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
    args = parser.parse_args()

    m3u_path = args.m3u
    ignore_names = {"fanduel", "bbc america sd", "bbc america hd", "bet hd"}

    # Fetch and filter live (tv.m3u)
    try:
        live_blocks, live_skipped = fetch_and_filter(LIVE_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {LIVE_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Backup.m3u
    try:
        backup_blocks, backup_skipped = fetch_and_filter(BACKUP_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {BACKUP_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter TheTVApp.m3u8
    try:
        tvapp_blocks, tvapp_skipped = fetch_and_filter(THETVAPP_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {THETVAPP_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Xumo playlist
    try:
        xumo_blocks, xumo_skipped = fetch_and_filter(XUMO_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {XUMO_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter LocalNow playlist
    try:
        localnow_blocks, localnow_skipped = fetch_and_filter(LOCALNOW_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {LOCALNOW_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Tubi playlist
    try:
        tubi_blocks, tubi_skipped = fetch_and_filter(TUBI_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {TUBI_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Roku playlist
    try:
        roku_blocks, roku_skipped = fetch_and_filter(ROKU_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {ROKU_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Pluto TV playlist
    try:
        pluto_blocks, pluto_skipped = fetch_and_filter(PLUTO_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {PLUTO_URL}: {e}", file=sys.stderr)
        return 1

    # Fetch and filter Plex playlist
    try:
        plex_blocks, plex_skipped = fetch_and_filter(PLEX_URL, ignore_names)
    except Exception as e:
        print(f"Error fetching {PLEX_URL}: {e}", file=sys.stderr)
        return 1

    # Deduplicate by display name (first occurrence wins)
    seen: set[str] = set()
    live_kept, seen, live_dup = dedupe_blocks_by_name(live_blocks, seen)
    backup_kept, seen, backup_dup = dedupe_blocks_by_name(backup_blocks, seen)
    tvapp_kept, seen, tvapp_dup = dedupe_blocks_by_name(tvapp_blocks, seen)
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
