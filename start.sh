#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "ابتدا .env.example را به .env کپی کنید و BOT_TOKEN را وارد کنید."
  exit 1
fi
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python bot.py
