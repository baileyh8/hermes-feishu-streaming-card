#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# setup.sh — quick start script
# Usage: bash setup.sh [--greeting "Your greeting"]
# ─────────────────────────────────────────────────────────────────

set -e

GREETING="${1:---greeting}"

# Detect hermes dir
HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"

if [ ! -d "$HERMES_DIR" ]; then
    echo "Error: hermes-agent not found at $HERMES_DIR"
    echo "Set HERMES_DIR environment variable or use --hermes-dir"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Feishu Streaming Card Installer"
echo "  hermes-dir: $HERMES_DIR"
echo ""

# Install dependencies
echo "Installing dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt" -q

# Run installer
echo ""
echo "Running installer..."
python "$SCRIPT_DIR/installer.py" --hermes-dir "$HERMES_DIR" $GREETING

echo ""
echo "Done! Restart hermes to activate:"
echo "  cd $HERMES_DIR && source venv/bin/activate"
echo "  python -m hermes_cli.main gateway restart"
