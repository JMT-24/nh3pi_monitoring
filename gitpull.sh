#!/bin/bash
# Cron auto-pull for the Pi. Install with:
#   crontab -e
#   */5 * * * * /home/nh3/nh3pi_monitoring/gitpull.sh >> /home/nh3/gitpull.log 2>&1
#
# NOTE: this only updates the code on disk. It does NOT restart controller.py — a
# running loop keeps using the old code until it is restarted (see the README).
set -euo pipefail

# Resolve the repo from THIS script's location rather than hardcoding a path: the
# hardcoded one drifted from the actual clone directory, and with no `cd` guard a
# failed cd left `git pull` running against whatever repo happened to be in cron's $HOME.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

git pull origin main
