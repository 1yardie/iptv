#!/usr/bin/env bash
# Create /mnt/jellyfin/jellyfin/tv for Jellyfin (Live TV recordings + M3U path)
# and link this repo's tv-channels.m3u there so Jellyfin can use it as an M3U tuner.
# Run with sudo or as root.
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TV_DIR="/mnt/jellyfin/jellyfin/tv"
JELLYFIN_USER="${JELLYFIN_USER:-jellyfin}"
JELLYFIN_GROUP="${JELLYFIN_GROUP:-jellyfin}"

echo "Creating ${TV_DIR}..."
mkdir -p /mnt/jellyfin/jellyfin
mkdir -p "$TV_DIR"

echo "Setting ownership to ${JELLYFIN_USER}:${JELLYFIN_GROUP}..."
chown -R "${JELLYFIN_USER}:${JELLYFIN_GROUP}" /mnt/jellyfin/jellyfin

M3U_LINK="${TV_DIR}/main.m3u"
SOURCE="${REPO_ROOT}/main.m3u"
if [ ! -f "$SOURCE" ]; then
  echo "Warning: ${SOURCE} not found; skipping M3U link." >&2
elif [ -L "$M3U_LINK" ] || [ -e "$M3U_LINK" ]; then
  echo "Already exists: ${M3U_LINK}"
else
  echo "Linking main.m3u into Jellyfin TV folder..."
  ln -s "$SOURCE" "$M3U_LINK"
  chown -h "${JELLYFIN_USER}:${JELLYFIN_GROUP}" "$M3U_LINK"
fi

echo "Done. Jellyfin TV folder: ${TV_DIR}"
echo "  - Use ${TV_DIR} as Live TV recording path in Jellyfin (Dashboard → Live TV → DVR)."
echo "  - Add M3U tuner with path: ${M3U_LINK} (Dashboard → Live TV → Tuners)."
