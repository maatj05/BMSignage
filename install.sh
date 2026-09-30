#!/bin/sh
# Installs dependencies and the systemd service on Raspberry Pi OS (Bookworm).
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"

sudo apt-get update
sudo apt-get install -y mpv ffmpeg python3-requests v4l-utils

if [ ! -f "$DIR/settings.toml" ]; then
    cp "$DIR/settings.example.toml" "$DIR/settings.toml"
fi

sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" \
    "$DIR/systemd/dropbox-signage.service" | sudo tee /etc/systemd/system/dropbox-signage.service >/dev/null

# The login prompt on the screen would otherwise fight with the player.
sudo systemctl disable --now getty@tty1.service || true

sudo systemctl daemon-reload
sudo systemctl enable dropbox-signage.service
sudo systemctl restart dropbox-signage.service
IP="$(hostname -I | cut -d' ' -f1)"
echo "Klaar. Stel het scherm in via http://$IP:8080"
echo "Logboek bekijken: journalctl -u dropbox-signage -f"
