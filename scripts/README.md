# Server scripts

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
