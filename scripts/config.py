"""
config.py — single place for everything that's specific to a household, rather than to the
model logic itself. Ships with synthetic demo values so the repo works unmodified out of the
box. To point this at your own real Tiller sheet, copy this file to config_local.py (gitignored)
and override whatever you need there — render_*.py never needs to change.

  cp scripts/config.py scripts/config_local.py
  # edit config_local.py: SHEET_ID, HOUSEHOLD_NAME, your real pension/SS category names, ...
"""
import os

# ── Identity — shown on every page and in the PDF letterhead/footer ──────────────────────────
HOUSEHOLD_NAME = 'Alex & Jordan'
ADVISOR_NAME = 'Pat Chen, CPA'

# ── Where the data lives ──────────────────────────────────────────────────────────────────────
# SHEET_ID is what the scripts actually connect with (the long string in the sheet's URL, between
# /d/ and /edit) -- addressing the sheet directly like this means the service account credential
# only needs the narrow "spreadsheets" OAuth scope, not "drive" (which would let it search your
# whole Drive by filename). SHEET_NAME is cosmetic only, for docs/error messages -- not used to
# connect. See "Understanding the security model" in SETUP_GOOGLE_SHEETS.md.
SHEET_ID = '1-pdkeSihXshh_2BB8BLRRpAGoAKfII8XK6R6Us3xAaY'
SHEET_NAME = 'Tiller Mockup'
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Guaranteed-income tax estimate — a RATE, not a frozen dollar figure, applied fresh to each
# window's actual income. These are illustrative example rates, not tax advice — see README.
SSPENSION_CATS = ('Alex Social Security', 'Jordan Social Security', 'Alex Pension', 'Jordan Pension')
PENSION_CATS = ('Alex Pension', 'Jordan Pension')
TAX_RATE_FED = 0.135
TAX_RATE_CA = 0.093

# ── PDF generation ────────────────────────────────────────────────────────────────────────────
# Headless Chrome/Chromium binary used for the print-to-PDF step. Override via env var if your
# install lives somewhere else (Linux/Windows paths differ from the macOS default below).
CHROME = os.environ.get('TILLER_CHROME_PATH') or (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' if os.uname().sysname == 'Darwin'
    else '/usr/bin/google-chrome')

try:
    from config_local import *  # noqa: F401,F403  (gitignored — your real household's overrides)
except ImportError:
    pass
