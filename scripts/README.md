# Server scripts

## Hourly channel health checks on Linux

This is a read-only monitor for the latest GitHub `main.m3u`. It does not change
your playlist, remove channels, renew Pluto links, or push to GitHub. GitHub Actions
continues to handle playlist publication and link renewal.

Requires Linux with systemd and `/usr/bin/python3` version 3.9 or newer. No pip
packages, FFmpeg, GitHub token, or root-running service are needed. Run the installer
as your normal login user; it may ask for sudo once to enable your user service at
boot and after logout.

From an up-to-date clone of this repository on your Linux PC:

```bash
bash scripts/install-channel-checker.sh
```

The installer copies the checker to `~/.local/share/iptv-channel-checker/`, starts
a first scan, and enables a systemd user timer at **12 minutes past every hour**
in the PC's local timezone (within a 30-second scheduling window). A missed timer
run is caught up when the timer starts again. Runs do not overlap. The PC needs
internet access; an unreadable source playlist preserves the last completed report.
Rerun the installer after pulling checker code updates; it stops any active scan
before updating its installed copy. The source playlist is fetched anew on every
scan, without requiring a Git pull.

```bash
# Schedule and current service status
systemctl --user list-timers iptv-channel-check.timer
systemctl --user status iptv-channel-check.service

# Follow progress (Ctrl+C stops viewing logs, not the checker)
journalctl --user -u iptv-channel-check.service -f

# Run another scan now
systemctl --user start --no-block iptv-channel-check.service

# Open the latest report on a Linux desktop
xdg-open ~/.local/state/iptv-channel-checker/latest.html
```

Reports are private to your login user, with no web server or open network port:

- `latest.html`: searchable channel report, with failures first. Reopen/reload after a scan.
- `latest.csv` and `latest.json`: full results, without stream URLs or session tokens.
- `run-status.json`: start/completion/error status of the most recent attempt. A killed
  run may remain marked `running`; check systemd and the journal if the timestamp is old.
- `history/`: the last 168 completed reports (approximately one week at hourly cadence).

On a headless server, copy `latest.html` to your desktop with SCP or SFTP and open it
there. Check the completion timestamp: an old report is not a current health result.

### What the results mean

| Status | Meaning |
| --- | --- |
| `sample_ok` | A manifest and recognized media sample were received, or a direct media sample was received. |
| `suspect` | One scan failed, including its retry. |
| `failing` | At least two scans failed since the last successful sample. |
| `recovered` | A previously failing/suspect feed now passes. |
| `unchecked` | Rate limited, scan budget reached, unsupported/encrypted media, or an inconclusive response. Not evidence of a dead channel. |

Checks use up to six workers and space requests to the same host by at least one
second. Plex and Pluto subdomains are grouped for throttling. A provider returning
HTTP 429 is deferred for the rest of the scan; an inconclusive check does not add
to or clear a channel's failure count. HTTP/network failures are retried once.
Exact duplicate feeds with identical player settings are checked once per run.
Failure history uses channel identity rather than expiring stream tokens.

HLS checks select a low-bandwidth video variant and inspect up to 64 KiB from a
recent segment. They recognize MPEG-TS and MP4 framing; they **do not decode video**
or verify correct programming, audio, moving pictures, ad transitions, or sustained
TiviMate playback. A frozen stream can therefore pass. Encrypted HLS, DASH,
byte-range HLS, and player-specific settings are reported as needing a player check.
Access-denied responses mean this PC could not access the feed, not that it is
permanently unavailable everywhere.

For roughly 2,000 channels, one 64 KiB sample per channel is about 125 MiB per scan,
plus manifests and retries (around 3 GiB/day for samples alone at hourly cadence).
Actual traffic varies; `response_bytes_read` in the JSON report records response
body bytes read. Scans have a 45-minute budget; entries not reached are `unchecked`.
The service has a 50-minute hard timeout. Look at the first full scan to see whether
your connection and providers allow all channels to be checked within an hour.

### Configuration and removal

For a manual small trial, use a separate output directory so it does not replace
the full report or its history:

```bash
python3 scripts/check_channels.py --limit 10 --output /tmp/iptv-check-trial
```

To change the playlist, concurrency, or budget, run
`systemctl --user edit iptv-channel-check.service` and override `ExecStart`, retaining
the installed script and output paths. For example:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 "%h/.local/share/iptv-channel-checker/check_channels.py" --output "%h/.local/state/iptv-channel-checker" --workers 4 --timeout 10 --host-spacing 2 --max-minutes 45
```

Use `--playlist URL_OR_PATH` for a different playlist. Keep the scan budget below
the service's 50-minute timeout. Changing configuration does not alter the playlist.

To disable checks and remove the installed units/code (reports are kept):

```bash
systemctl --user disable --now iptv-channel-check.timer
systemctl --user stop iptv-channel-check.service
rm ~/.config/systemd/user/iptv-channel-check.{timer,service}
rm ~/.local/share/iptv-channel-checker/check_channels.py
systemctl --user daemon-reload
```

The installer enables lingering for your account. Leave it enabled if you have
other background user services; otherwise it can be disabled with
`sudo loginctl disable-linger "$USER"`.

Scheduling behavior follows the [systemd timer documentation](https://manpages.debian.org/trixie/systemd/systemd.timer.5.en.html)
and [loginctl lingering documentation](https://manpages.debian.org/trixie/systemd/loginctl.1.en.html).

---

## Jellyfin TV folder (`/mnt/jellyfin/jellyfin/tv`)

Creates the TV directory for Jellyfin and links this repo’s `main.m3u` so Jellyfin can use it as an M3U tuner.

**Run on the server (sudo required):**
```bash
cd /path/to/iptv
sudo ./scripts/setup-jellyfin-tv.sh
```

This will:
- Create `/mnt/jellyfin/jellyfin/tv`
- Set ownership to `jellyfin:jellyfin` (override with `JELLYFIN_USER` / `JELLYFIN_GROUP` if needed)
- Symlink `main.m3u` → `/mnt/jellyfin/jellyfin/tv/main.m3u`

**In Jellyfin:**
- **Dashboard → Live TV → DVR:** set recording path to `/mnt/jellyfin/jellyfin/tv` (or a subfolder like `recordings`).
- **Dashboard → Live TV → Tuners:** add M3U tuner, path `/mnt/jellyfin/jellyfin/tv/main.m3u`.

---

## Hourly sync live → main.m3u and push to GitHub

From the repo root on your server:

1. **One-time setup**
   - Clone the repo (or pull) so you have `sync_buddylive_to_tv_channels.py` and `scripts/sync-live-hourly.sh`.
   - Ensure Git can push (SSH key or token). Example:
     ```bash
     git remote -v
     git push   # test once
     ```

2. **Run hourly with cron**
   ```bash
   crontab -e
   ```
   Add this line (runs at minute 0 of every hour; use the full path to your repo):
   ```cron
   0 * * * * /full/path/to/iptv/scripts/sync-live-hourly.sh >> /tmp/sync-live.log 2>&1
   ```

3. **Test once by hand**
   ```bash
   cd /path/to/iptv
   ./scripts/sync-live-hourly.sh
   ```
