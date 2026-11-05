#!/bin/bash
cd "$(dirname "$0")" || exit 1
exec ./venv/bin/python scripts/start_panel.py
