#!/usr/bin/env bash
# Run as your normal Linux login user, not with sudo.
set -euo pipefail

if [[ $(uname -s) != Linux || $EUID == 0 ]]; then
  echo "Run this installer as your normal login user on Linux (without sudo)." >&2
  exit 1
fi
command -v systemctl >/dev/null
command -v loginctl >/dev/null
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9 or newer is required"'
systemctl --user show-environment >/dev/null
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
app_dir="$HOME/.local/share/iptv-channel-checker"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$app_dir" "$unit_dir" "$HOME/.local/state/iptv-channel-checker"
chmod 700 "$HOME/.local/state/iptv-channel-checker"

# A user service stays available after logout and starts at boot with lingering.
if [[ $(loginctl show-user "$(id -un)" --property=Linger --value) != yes ]]; then
  echo "Enabling your systemd user manager at boot and after logout (sudo may ask for your password)."
  sudo loginctl enable-linger "$(id -un)"
fi

# Stop only this checker's prior run before replacing its installed copy.
systemctl --user stop iptv-channel-check.timer iptv-channel-check.service 2>/dev/null || true
install -m 644 "$script_dir/check_channels.py" "$app_dir/check_channels.py"
install -m 644 "$script_dir/systemd/iptv-channel-check.service" "$unit_dir/iptv-channel-check.service"
install -m 644 "$script_dir/systemd/iptv-channel-check.timer" "$unit_dir/iptv-channel-check.timer"
systemctl --user daemon-reload
systemctl --user enable --now iptv-channel-check.timer
systemctl --user start --no-block iptv-channel-check.service
systemctl --user list-timers iptv-channel-check.timer --no-pager
echo "First scan started. Reports: $HOME/.local/state/iptv-channel-checker/latest.html"
echo "Progress: journalctl --user -u iptv-channel-check.service -f"
