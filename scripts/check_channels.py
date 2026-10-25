#!/usr/bin/env python3
"""Read-only, bounded HLS reachability audit. No third-party Python packages."""

import argparse
import collections
import concurrent.futures
import csv
import datetime as dt
import fcntl
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PLAYLIST = "https://raw.githubusercontent.com/1yardie/iptv/refs/heads/main/main.m3u"
MANIFEST_LIMIT = 1024 * 1024
SAMPLE_LIMIT = 64 * 1024


class CheckError(Exception):
    def __init__(self, kind, reason):
        self.kind, self.reason = kind, reason


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_playlist(text):
    if not text.lstrip("\ufeff\r\n ").startswith("#EXTM3U"):
        raise ValueError("The playlist response is not an M3U playlist")
    entries, counts = [], collections.Counter()
    section, current = "", None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# ==="):
            section = line.removeprefix("# ===").removesuffix("===").strip()
        elif line.startswith("#EXTINF:"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            current = {"name": line.rsplit(",", 1)[-1].strip(),
                       "section": section, "guide_id": attrs.get("tvg-id", ""),
                       "group": attrs.get("group-title", ""), "headers": {},
                       "unsupported": False}
        elif current is not None:
            if line.startswith("#EXTVLCOPT:http-user-agent="):
                current["headers"]["User-Agent"] = line.split("=", 1)[1]
            elif line.startswith("#EXTVLCOPT:http-referrer="):
                current["headers"]["Referer"] = line.split("=", 1)[1]
            elif line.startswith("#EXTHTTP:"):
                try:
                    current["headers"].update(json.loads(line.split(":", 1)[1]))
                except (ValueError, TypeError):
                    current["unsupported"] = True
            elif line.startswith("#KODIPROP:"):
                current["unsupported"] = True
            elif line and not line.startswith("#"):
                url, sep, headers = line.partition("|")
                if sep:
                    current["headers"].update(urllib.parse.parse_qsl(headers))
                current["url"] = url
                key = json.dumps([section, current["guide_id"], current["name"]])
                counts[key] += 1
                # Session tokens change hourly; identity must not depend on them.
                current["id"] = hashlib.sha256(f"{key}:{counts[key]}".encode()).hexdigest()[:24]
                entries.append(current)
                current = None
    if not entries:
        raise ValueError("The playlist contains no channel entries")
    return entries


def provider(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for domain in ("plex.tv", "pluto.tv"):
        if host == domain or host.endswith("." + domain):
            return domain
    return host


class Network:
    def __init__(self, timeout, deadline, spacing):
        self.timeout, self.deadline, self.spacing = timeout, deadline, spacing
        self.guard = threading.Lock()
        self.locks, self.next_request, self.limited = {}, {}, set()
        self.bytes_read = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CheckError("unchecked", "Scan time budget reached")
        return remaining

    def get(self, url, headers, limit, complete=False, stream=False):
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise CheckError("unchecked", "Unsupported stream URL scheme")
        host = provider(url)
        with self.guard:
            lock = self.locks.setdefault(host, threading.Lock())
        if not lock.acquire(timeout=self.remaining()):
            raise CheckError("unchecked", "Scan time budget reached")
        try:
            if host in self.limited:
                raise CheckError("unchecked", "Provider rate limited; deferred until next scan")
            wait = self.next_request.get(host, 0) - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, self.remaining()))
            timeout = min(self.timeout, self.remaining())
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **headers})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    # read1 avoids waiting for a complete live stream and lets us
                    # recheck the deadline even with slowly delivered bodies.
                    data = bytearray()
                    while len(data) < limit:
                        self.remaining()
                        chunk = response.read1(min(16384, limit - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if stream and len(data) >= SAMPLE_LIMIT and not data.lstrip().startswith(b"#EXTM3U"):
                            break
                    with self.guard:
                        self.bytes_read += len(data)
                    if complete and len(data) == limit:
                        raise CheckError("unchecked", "Manifest exceeds safety size limit")
                    return bytes(data), response.url
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code == 429:
                    self.limited.add(host)
                    raise CheckError("unchecked", "HTTP 429; provider deferred until next scan") from None
                raise CheckError("failed", f"HTTP {code}") from None
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
                # Exception strings can contain signed URLs; never persist them.
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, socket.gaierror):
                    label = "DNS lookup failed"
                elif isinstance(reason, (TimeoutError, socket.timeout)):
                    label = "Connection or read timed out"
                elif isinstance(reason, ssl.SSLError):
                    label = "TLS connection failed"
                else:
                    label = "Network request failed"
                raise CheckError("failed", label) from None
        finally:
            self.next_request[host] = time.monotonic() + self.spacing
            lock.release()


def media_kind(data):
    for offset in range(min(188, max(0, len(data) - 376))):
        if data[offset] == data[offset + 188] == data[offset + 376] == 0x47:
            return "MPEG-TS"
    if len(data) >= 16 and data[4:8] in (b"ftyp", b"styp", b"moof", b"sidx", b"emsg"):
        return "MP4"
    return None


def probe(entry, network):
    if entry["unsupported"]:
        raise CheckError("unchecked", "Player-specific settings require a player check")
    url, headers = entry["url"], entry["headers"]
    for _ in range(5):
        data, final_url = network.get(url, headers, MANIFEST_LIMIT, complete=True, stream=True)
        if not data.lstrip().startswith(b"#EXTM3U"):
            if media_kind(data):
                return "Direct media sample received"
            raise CheckError("unchecked", "Not HLS or recognized media; requires a player check")
        lines = [line.strip() for line in data.decode("utf-8-sig", errors="replace").splitlines()]
        variants = []
        for i, line in enumerate(lines[:-1]):
            if line.startswith("#EXT-X-STREAM-INF:"):
                bandwidth = re.search(r'(?:[:,])BANDWIDTH=(\d+)', line)
                candidate = lines[i + 1]
                if candidate and not candidate.startswith("#"):
                    variants.append((0 if "RESOLUTION=" in line else 1,
                                     int(bandwidth.group(1)) if bandwidth else 0, candidate))
        if variants:
            url = urllib.parse.urljoin(final_url, min(variants)[2])
            continue
        if any(line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line for line in lines):
            raise CheckError("unchecked", "Encrypted HLS requires a player check")
        if any(line.startswith("#EXT-X-BYTERANGE:") for line in lines):
            raise CheckError("unchecked", "Byte-range HLS requires a player check")
        for line in lines:
            if line.startswith("#EXT-X-MAP:"):
                init_uri = re.search(r'URI="([^"]+)"', line)
                if "BYTERANGE=" in line or not init_uri:
                    raise CheckError("unchecked", "HLS initialization requires a player check")
                init, _ = network.get(urllib.parse.urljoin(final_url, init_uri.group(1)), headers, SAMPLE_LIMIT)
                if media_kind(init) != "MP4":
                    raise CheckError("unchecked", "Unrecognized initialization bytes; requires a player check")
        segments, gap = [], False
        for line in lines:
            if line == "#EXT-X-GAP":
                gap = True
            elif line and not line.startswith("#"):
                if not gap:
                    segments.append(urllib.parse.urljoin(final_url, line))
                gap = False
        if not segments:
            raise CheckError("failed", "HLS playlist has no available segments")
        # Avoid the newest segment (which may still be publishing), then try
        # another recent segment if the first has just rolled off the server.
        candidates = list(reversed(segments[:-1] or segments))[:2]
        last_error = None
        for segment in candidates:
            try:
                sample, _ = network.get(segment, headers, SAMPLE_LIMIT)
                kind = media_kind(sample)
                if kind:
                    return f"HLS manifest and {kind} sample received"
                last_error = CheckError("unchecked", "Unrecognized segment bytes; requires a player check")
            except CheckError as exc:
                if exc.kind == "unchecked":
                    raise
                last_error = exc
        raise last_error
    raise CheckError("unchecked", "Too many nested HLS playlists")


def check_entry(entry, network):
    started = time.monotonic()
    for attempt in range(2):
        try:
            reason = probe(entry, network)
            kind = "sample_ok"
            break
        except CheckError as exc:
            kind, reason = exc.kind, exc.reason
            if kind == "unchecked" or attempt == 1:
                break
            time.sleep(min(3, max(0, network.deadline - time.monotonic())))
        except Exception:
            kind, reason = "unchecked", "Unexpected checker error; inspect implementation"
            break
    return {"result": kind, "reason": reason, "attempts": attempt + 1,
            "elapsed_seconds": round(time.monotonic() - started, 2)}


def apply_history(entry, result, previous):
    old = previous.get(entry["id"], {})
    streak = old.get("failure_streak", 0)
    last_ok = old.get("last_ok")
    status = result["result"]
    if status == "failed":
        streak += 1
        status = "failing" if streak >= 2 else "suspect"
    elif status == "sample_ok":
        status = "recovered" if streak else "sample_ok"
        streak, last_ok = 0, utc_now()
    # Inconclusive checks neither reset nor increment the failure count.
    return {key: entry[key] for key in ("id", "name", "section", "guide_id", "group")} | {
        **result, "status": status, "failure_streak": streak, "last_ok": last_ok}


def atomic_write(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_reports(output, report):
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    # latest.json is both the report and the committed state for the next scan.
    atomic_write(output / "latest.json", serialized)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history = output / "history"
    history.mkdir(exist_ok=True)
    atomic_write(history / f"{stamp}.json", serialized)
    for old in sorted(history.glob("*.json"))[:-168]:
        old.unlink()
    rows = report["channels"]
    fields = ["name", "section", "guide_id", "status", "reason", "failure_streak", "last_ok"]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Prevent spreadsheet formula execution from untrusted channel names.
        writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                         else value for key, value in row.items()})
    atomic_write(output / "latest.csv", stream.getvalue())
    priority = {"failing": 0, "suspect": 1, "unchecked": 2, "recovered": 3, "sample_ok": 4}
    table = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(key) or ''))}</td>" for key in fields) + "</tr>"
                    for row in sorted(rows, key=lambda row: (priority[row["status"]], row["name"])))
    counts = html.escape(json.dumps(report["counts"]))
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Channel health</title>
<style>body{{font:15px system-ui;margin:2rem;color:#17212b}}table{{border-collapse:collapse;width:100%}}
th,td{{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}}th{{background:#eef2f6;position:sticky;top:0}}
input{{padding:.7rem;width:min(90%,32rem)}}.scroll{{overflow:auto}}small{{color:#52606d}}</style>
<h1>Channel health</h1><p>Completed {html.escape(report['finished_at'])} (UTC).</p><p>{counts}</p>
<p>A sample passing means the feed was reachable. It does not confirm correct content, sustained playback,
or ad transitions. Suspect means one failed scan; failing means two or more failed scans since the last
successful sample. Unchecked means inconclusive, rate limited, or not reached within the scan budget.</p>
<input id="search" aria-label="Filter channels" placeholder="Filter by channel, section, or status">
<div class="scroll"><table><thead><tr>{''.join('<th>'+html.escape(key.replace('_',' ').title())+'</th>' for key in fields)}</tr></thead>
<tbody>{table}</tbody></table></div><script>
document.querySelector('#search').addEventListener('input', e => {{
for (const row of document.querySelectorAll('tbody tr')) row.hidden = !row.textContent.toLowerCase().includes(e.target.value.toLowerCase());
}});</script></html>"""
    atomic_write(output / "latest.html", document)


def run(args):
    output = Path(args.output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A scan is already running; skipping.")
            return 0
        started = utc_now()
        run_status = {"started_at": started, "status": "running"}
        atomic_write(output / "run-status.json", json.dumps(run_status) + "\n")
        try:
            network = Network(args.timeout, time.monotonic() + args.max_minutes * 60, args.host_spacing)
            if args.playlist.startswith(("https://", "http://")):
                data, _ = network.get(args.playlist, {}, 16 * MANIFEST_LIMIT, complete=True)
                text = data.decode("utf-8-sig")
            else:
                text = Path(args.playlist).read_text(encoding="utf-8-sig")
            entries = parse_playlist(text)
            if args.limit:
                entries = entries[:args.limit]
            previous_path = output / "latest.json"
            previous_report = json.loads(previous_path.read_text()) if previous_path.exists() else {}
            previous = {row["id"]: row for row in previous_report.get("channels", [])}
            # Share results for exact duplicate URLs with identical player settings.
            groups = collections.defaultdict(list)
            for entry in entries:
                key = (entry["url"], json.dumps(entry["headers"], sort_keys=True), entry["unsupported"])
                groups[key].append(entry)
            # Interleave providers so a slow provider cannot monopolize all workers.
            buckets = collections.defaultdict(collections.deque)
            for key, group in groups.items():
                buckets[provider(group[0]["url"])].append(key)
            ordered = []
            while buckets:
                for host in list(buckets):
                    ordered.append(buckets[host].popleft())
                    if not buckets[host]:
                        del buckets[host]
            results = {}
            print(f"Checking {len(entries)} entries / {len(groups)} distinct feeds", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                pending = {executor.submit(check_entry, groups[key][0], network): key for key in ordered}
                for n, future in enumerate(concurrent.futures.as_completed(pending), 1):
                    result = future.result()
                    for entry in groups[pending[future]]:
                        results[entry["id"]] = apply_history(entry, result, previous)
                    if n % 100 == 0:
                        print(f"Checked {n}/{len(groups)} feeds", flush=True)
            channels = [results[entry["id"]] for entry in entries]
            report = {"schema_version": 1, "started_at": started, "finished_at": utc_now(),
                      "playlist_sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "sample_bytes_limit": SAMPLE_LIMIT, "response_bytes_read": network.bytes_read,
                      "counts": dict(collections.Counter(row["status"] for row in channels)), "channels": channels}
            write_reports(output, report)
            run_status.update(status="completed", finished_at=report["finished_at"], counts=report["counts"])
            print(json.dumps(report["counts"]), flush=True)
        except Exception as exc:
            # Preserve the last completed channel report on download/state errors.
            reason = exc.reason if isinstance(exc, CheckError) else type(exc).__name__
            run_status.update(status="error", finished_at=utc_now(), reason=reason)
            print(f"Scan failed: {reason}; previous channel report retained", flush=True)
            return 1
        finally:
            atomic_write(output / "run-status.json", json.dumps(run_status, indent=2) + "\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist", default=DEFAULT_PLAYLIST)
    parser.add_argument("--output", default="./channel-health")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--host-spacing", type=float, default=1)
    parser.add_argument("--max-minutes", type=float, default=45)
    parser.add_argument("--limit", type=int, help="Check only the first N entries; use a separate output directory")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or min(args.timeout, args.host_spacing, args.max_minutes) <= 0 or (args.limit is not None and args.limit < 1):
        parser.error("Use 1–16 workers and positive timeout, spacing, budget, and limit")
    os.umask(0o077)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
