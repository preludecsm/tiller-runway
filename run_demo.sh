#!/usr/bin/env bash
# Regenerates the reports from whatever's currently in the Google Sheet named in scripts/config.py
# (SHEET_NAME). Run scripts/generate_demo_data.py first if the sheet doesn't have data in it yet.
#
# Two groups of reports, because they need different things from your sheet:
#
#   CORE      — need only the standard Tiller tabs (Transactions, Categories, Balance History,
#               Accounts), plus the Bucket/Tier columns you add to Categories. These always run.
#   OPTIONAL  — need a custom tab ("Downsize Model" / "Portfolio Model") that a stock Tiller sheet
#               doesn't have. If the tab is absent the script exits 3 and we skip it, so a fresh
#               setup still gets the core reports instead of failing outright.
#
# The advisor PDF draws on both optional models, so it's built only when both are present.
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python3"

CORE="render_dashboard render_tiers render_spending"
OPTIONAL="render_downsize render_sustainability"
skipped=""

run() {   # run <script>; returns 3 if it skipped itself for a missing custom tab
  echo "── $1 ──"
  set +e; "$PY" "scripts/$1.py"; local rc=$?; set -e
  [ $rc -ne 0 ] && [ $rc -ne 3 ] && exit $rc
  sleep 8   # each script re-reads the sheet from scratch; back-to-back runs can trip the
            # Sheets API's per-minute read quota otherwise
  return $rc
}

for script in $CORE; do run "$script" || true; done

for script in $OPTIONAL; do
  if ! run "$script"; then skipped="$skipped $script"; fi
done

if [ -z "$skipped" ]; then
  echo "── render_advisor_pdf ──"
  "$PY" "scripts/render_advisor_pdf.py"
else
  echo "── render_advisor_pdf ── skipped (needs both optional models)"
fi

echo
if [ -n "$skipped" ]; then
  echo "Skipped:$skipped — add the matching tab to your sheet to enable these."
  echo "See SETUP_GOOGLE_SHEETS.md. Everything else was generated."
  echo
fi
echo "Done. Open reports/dashboard.html in a browser."
