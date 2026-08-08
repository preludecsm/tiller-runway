#!/usr/bin/env bash
# Regenerates every report (5 HTML pages + the advisor PDF) from whatever's currently in the
# Google Sheet named in scripts/config.py (SHEET_NAME). Run scripts/generate_demo_data.py first
# if the sheet doesn't have data in it yet.
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python3"

for script in render_dashboard render_tiers render_spending render_downsize render_sustainability; do
  echo "── $script ──"
  "$PY" "scripts/$script.py"
  sleep 8   # each script re-reads the sheet from scratch; back-to-back runs can trip the
            # Sheets API's per-minute read quota otherwise
done

echo "── render_advisor_pdf ──"
"$PY" "scripts/render_advisor_pdf.py"

echo
echo "Done. Open reports/dashboard.html in a browser, or reports/advisor_summary.pdf."
