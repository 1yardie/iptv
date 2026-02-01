# TV Guide (EPG) Not Working – Troubleshooting

Your playlist `streams/distro_new.m3u` uses this EPG:

- **URL:** `https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz`
- **Format:** XMLTV, gzip-compressed (`.xml.gz`)

Channel IDs in the EPG (e.g. `Lone.Star.distro`, `hi.Drama!.distro`) match the `tvg-id` values in the M3U, so ID mismatch is not the cause.

---

## Likely causes and what to do

### 1. **App ignores `url-tvg` from the M3U**
Many apps do **not** read the EPG URL from the `#EXTM3U url-tvg="..."` line. They expect you to set the EPG URL in app settings.

- **Fix:** In your IPTV app, open **Settings → EPG / TV Guide** and set the EPG URL manually to:
  ```text
  https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz
  ```
  Then assign this EPG source to the playlist or refresh the guide.

### 2. **App doesn’t support gzipped EPG (`.xml.gz`)**
Some players only accept plain `.xml` and fail on `.xml.gz`. epgshare01 only provides the DistroTV EPG as `.xml.gz`.

- **Fix:** Use an app that supports gzipped XMLTV (e.g. TiviMate, Kodi with PVR IPTV Simple, VLC 3.x, or IPTVnator). If your app has a “EPG compression” or “Gzip” option, enable it.

### 3. **EPG not refreshed**
The app may be using an old or empty EPG cache.

- **Fix:** In the app, run **Refresh EPG** / **Update TV Guide** (and clear EPG cache if available). Wait for the download to finish; the file is ~7.6 MB compressed.

### 4. **EPG URL unreachable from the app**
The app might be unable to reach the URL (network, firewall, or the site blocking non-browser requests).

- **Check:** On the same network, open this in a browser; the file should download:
  ```text
  https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz
  ```
  If it doesn’t download, the problem is network or the source. If it does, the issue is likely app/device.

### 5. **EPG source or schedule empty**
Rarely, the EPG file can be temporarily empty or mis-generated.

- **Check:** epgshare01 provides a channel list here (IDs should match your M3U):
  ```text
  https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.txt
  ```

---

## Quick checklist

| Step | Action |
|------|--------|
| 1 | Set EPG URL **manually** in the app to the `.xml.gz` URL above (don’t rely only on `url-tvg`). |
| 2 | Use an app that supports **gzipped** XMLTV. |
| 3 | Run **Refresh EPG** / **Update TV Guide** and wait for the full download. |
| 4 | Confirm the EPG URL downloads in a browser on the same network. |

If you tell me which app or device you use (e.g. TiviMate, Kodi, VLC, Smart TV app), I can give app-specific steps.
