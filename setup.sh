#!/bin/bash
# setup.sh — Create virtualenv and install dependencies
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  python3 -m venv venv
  echo "Virtualenv created."
fi

venv/bin/pip install -q -r requirements.txt
echo "Dependencies installed."
echo ""
echo "Ready. Run tools with:"
echo "  venv/bin/python tools/search_and_scrape.py --query \"Acme Corp Denver\" --type contact"
echo "  venv/bin/python tools/search_and_scrape.py --query \"Jane Smith Austin TX\" --type person"
echo "  venv/bin/python tools/scrape_single_site.py --url https://example.com"
