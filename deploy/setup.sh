#!/usr/bin/env bash
# Set up a fresh Ubuntu 24.04 EC2 instance to run the recorder, as ubuntu, from its home directory:
#     bash setup.sh
# Installs the system packages, clones the repo to ~/CopyTrader, installs its Python packages for ubuntu, runs the tests,
# and installs the service, enabled so it starts on boot, but not started: the database goes in data/ first, see
# commands.txt. Safe to run again, as after an update of this script.

set -euo pipefail

REPO=https://github.com/ryubears/CopyTrader.git
DIR="$HOME/CopyTrader"
SERVICE=copy-trader

sudo apt-get update -q
sudo apt-get install -y -q python3-pip git sqlite3 zstd
[ -d "$DIR/.git" ] || git clone "$REPO" "$DIR"
cd "$DIR"
git pull --ff-only
pip3 install --user --break-system-packages -q -r requirements.txt
mkdir -p data/logs
python3 -m pytest -q
sudo cp "deploy/$SERVICE.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
echo "Set up. Put the database in $DIR/data, then: sudo systemctl start $SERVICE"
