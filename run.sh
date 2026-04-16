#!/bin/bash
# Start the Polymarket bot — run this from anywhere
cd "$(dirname "$0")"
source .venv/bin/activate
python main.py
