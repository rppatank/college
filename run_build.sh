#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "Building local college store..."
python scripts/build_db.py
echo "Done."