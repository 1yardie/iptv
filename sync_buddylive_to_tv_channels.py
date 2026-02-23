#!/usr/bin/env python3
"""
Sync live (tv.m3u + Backup.m3u + TheTVApp.m3u8 + Xumo + LocalNow) into main.m3u.
- Fetches all five remote playlists
- Keeps only channels whose display name does NOT start with '[' and is not Fanduel
- Skips Fanduel (by name or tvg-id), BBC America SD/HD, BET HD, and any stream URL containing moveonjoy
- Writes main.m3u as a fresh file: live + Backup + TheTVApp + Xumo + LocalNow sections
"""
import argparse
import sys
import urllib.request
from pathlib import Path

LIVE_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/tv.m3u"
BACKUP_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/Backup.m3u"
THETVAPP_URL = "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/TheTVApp.m3u8"
XUMO_URL = "https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/refs/heads/main/playlists/xumo_playlist.m3u"
LOCALNOW_URL = "https://www.apsattv.com/localnow.m3u"
LIVE_SECTION = "# === live ==="
BACKUP_SECTION = "# === Backup ==="
THETVAPP_SECTION = "# === TheTVApp ==="
XUMO_SECTION = "# === Xumo ==="
LOCALNOW_SECTION = "# === LocalNow ==="


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

    # Build fresh M3U: header + live + Backup + TheTVApp + Xumo + LocalNow
    out_lines = ["#EXTM3U", ""]
    out_lines.append(LIVE_SECTION)
    for block in live_blocks:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(BACKUP_SECTION)
    for block in backup_blocks:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(THETVAPP_SECTION)
    for block in tvapp_blocks:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(XUMO_SECTION)
    for block in xumo_blocks:
        out_lines.extend(block)
        out_lines.append("")
    out_lines.append(LOCALNOW_SECTION)
    for block in localnow_blocks:
        out_lines.extend(block)
        out_lines.append("")
    out_text = "\n".join(out_lines)
    if not out_text.endswith("\n"):
        out_text += "\n"

    total = len(live_blocks) + len(backup_blocks) + len(tvapp_blocks) + len(xumo_blocks) + len(localnow_blocks)
    if args.dry_run:
        print(f"Would write {total} channels to {m3u_path} (live: {len(live_blocks)}, Backup: {len(backup_blocks)}, TheTVApp: {len(tvapp_blocks)}, Xumo: {len(xumo_blocks)}, LocalNow: {len(localnow_blocks)}; skipped live: {live_skipped}, Backup: {backup_skipped}, TheTVApp: {tvapp_skipped}, Xumo: {xumo_skipped}, LocalNow: {localnow_skipped})")
        print(f"Output would be {len(out_lines)} lines")
        return 0

    m3u_path.write_text(out_text, encoding="utf-8")
    print(f"Synced {total} channels into {m3u_path} (live: {len(live_blocks)}, Backup: {len(backup_blocks)}, TheTVApp: {len(tvapp_blocks)}, Xumo: {len(xumo_blocks)}, LocalNow: {len(localnow_blocks)}; skipped live: {live_skipped}, Backup: {backup_skipped}, TheTVApp: {tvapp_skipped}, Xumo: {xumo_skipped}, LocalNow: {localnow_skipped})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
