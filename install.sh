#!/bin/sh
# Installs dependencies and the systemd service on Raspberry Pi OS (Bookworm).
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"

sudo apt-get update
sudo apt-get install -y mpv python3-requests v4l-utils

if [ ! -f "$DIR/settings.toml" ]; then
    cp "$DIR/settings.example.toml" "$DIR/settings.toml"
    echo "settings.toml aangemaakt: vul de Dropbox-gegevens in en start opnieuw."
fi

sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" \
    "$DIR/systemd/dropbox-signage.service" | sudo tee /etc/systemd/system/dropbox-signage.service >/dev/null

# The login prompt on the screen would otherwise fight with the player.
sudo systemctl disable --now getty@tty1.service || true

sudo systemctl daemon-reload
sudo systemctl enable dropbox-signage.service
sudo systemctl restart dropbox-signage.service
echo "Klaar. Logboek bekijken: journalctl -u dropbox-signage -f"
