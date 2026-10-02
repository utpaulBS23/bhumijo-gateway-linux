#!/bin/bash
# One-time setup of a fresh clone on the Pi:
#   cd <repo> && sudo bash facility-node/deploy/bootstrap.sh
# 1. turns on the repo's git hooks, so every `git pull` reinstalls + restarts
# 2. runs the installer
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Run as root: sudo bash facility-node/deploy/bootstrap.sh"; exit 1; }

NODE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$NODE/.." && pwd)"
OWNER="$(stat -c %U "$ROOT")"     # the user who cloned (usually pi)

if [ -d "$ROOT/.git" ]; then
    sudo -u "$OWNER" git -C "$ROOT" config core.hooksPath facility-node/deploy/githooks
    echo "git hooks on: every 'git pull' in $ROOT reinstalls the node"
else
    echo "!! $ROOT is not a git clone; auto-install on pull not enabled"
fi

bash "$NODE/deploy/install.sh"
