#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 gold_score_bot.py
