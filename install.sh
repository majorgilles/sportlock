#!/bin/bash
# Install sportlock for the current user: CLI symlink, systemd user unit.
set -euo pipefail
repo=$(cd "$(dirname "$0")" && pwd)

mkdir -p ~/.local/bin ~/.config/systemd/user
ln -sf "$repo/bin/sportlock" ~/.local/bin/sportlock
ln -sf "$repo/systemd/sportlock.service" ~/.config/systemd/user/sportlock.service

systemctl --user daemon-reload
systemctl --user enable --now sportlock.service
systemctl --user restart sportlock.service
echo "sportlock installed. Config: ~/.config/sportlock/config.toml (disabled until you set enabled = true)"
