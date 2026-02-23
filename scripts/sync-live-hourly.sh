#!/usr/bin/env bash
# Run hourly on the server: sync live into tv-channels.m3u and push to GitHub if changed.
set -e
cd "$(dirname "$0")/.."
python3 sync_buddylive_to_tv_channels.py
git add main.m3u
if ! git diff --staged --quiet; then
  git commit -m "chore: sync live into main.m3u"
  git push
fi
