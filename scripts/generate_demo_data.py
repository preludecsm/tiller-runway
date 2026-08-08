#!/usr/bin/env python3
"""
generate_demo_data.py — fabricate 3 years of synthetic household data, in the same shape a real
Tiller sheet has, and load it into the Google Sheet named in config.py (SHEET_NAME).

This is fake data for demo purposes only: made-up amounts for a made-up household ("Alex &
Jordan", per config.py). It exists to make every feature in the six render_*.py scripts show
something real-looking without needing anyone's actual financial data — a spending tier in each
of the four cut-order tiers, a few categories flagged as one-offs/volatile, a mortgage amortizing
down, guaranteed income starting partway through the window, and enough Downsize Model / Portfolio
Model assumptions for both Monte Carlo pages to run.

Usage:
  python3 generate_demo_data.py            # builds the data and writes it to the sheet named in
                                            # config.py (must already exist and be shared with the
                                            # service account — see SETUP_GOOGLE_SHEETS.md)
  python3 generate_demo_data.py --dry-run  # builds the data and just prints row counts, no writes
"""
import gspread, glob, os, random, sys
from google.oauth2.service_account import Credentials
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROJECT_ROOT, SHEET_NAME

os.chdir(PROJECT_ROOT)
random.seed(20260807)   # fixed seed -- the demo data is reproducible, not different every run

def monthstart(d): return date(d.year, d.month, 1)
def addmonths(d, n):
    y, m = d.year, d.month + n
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return date(y, m, 1)
def fmt(d): return d.strftime('%m/%d/%Y')

TODAY = date.today()
END_MONTH = monthstart(TODAY) - timedelta(days=1)   # last day of the last complete month
START_MONTH = addmonths(monthstart(END_MONTH), -35)  # 36 months back = three full 12-month windows
MONTHS = [addmonths(START_MONTH, i) for i in range(36)]  # oldest first

def jitter(base, pct=0.12):
    return round(base * (1 + random.uniform(-pct, pct)), 2)

def month_day(m, day=None):
    """A plausible transaction date within month m."""
    d = day or random.randint(1, 27)
    return date(m.year, m.month, min(d, 28))

# ── Categories ─────────────────────────────────────────────────────────────────────────────────
# (name, Type, Bucket, Tier) -- Tier '' left blank on purpose for one category, to exercise the
# "no Tier cell filled in -> defaults to Tier 1" fallback path in render_tiers.py.
CATEGORIES = [
    # Tier 0 -- floor, never cut
    ('Mortgage', 'Expense', 'Housing', '0'),
    ('Property Tax', 'Expense', 'Housing', '0'),
    ('Utilities', 'Expense', 'Housing', '0'),
    ('Groceries', 'Expense', 'Food & Dining', '0'),
    ('Healthcare', 'Expense', 'Healthcare', '0'),
    ('Auto & Gas', 'Expense', 'Transportation', '0'),
    # Tier 1 -- first to trim
    ('Dining Out', 'Expense', 'Food & Dining', '1'),
    ('Subscriptions', 'Expense', 'Subscriptions & Media', '1'),
    ('Personal Care', 'Expense', 'Shopping', '1'),
    ('Home Maintenance', 'Expense', 'Home Projects & Upkeep', '1'),
    ('Shopping', 'Expense', 'Shopping', ''),      # <- blank Tier, exercises the fallback
    # Tier 2 -- postponable
    ('Travel', 'Expense', 'Travel', '2'),
    ('Home Improvements', 'Expense', 'Home Projects & Upkeep', '2'),
    ('Education', 'Expense', 'Shopping', '2'),
    # Tier 3 -- purely elective
    ('Giving', 'Expense', 'Giving', '3'),
    ('Gifts', 'Expense', 'Giving', '3'),
    # excluded from the tier system entirely (cost of income/portfolio, not lifestyle)
    ('IRS', 'Expense', '', ''),
    ('CA Tax', 'Expense', '', ''),
    ('Investment Fees', 'Expense', '', ''),
    # income
    ('Alex Social Security', 'Income', '', ''),
    ('Jordan Social Security', 'Income', '', ''),
    ('Alex Pension', 'Income', '', ''),
    ('Jordan Pension', 'Income', '', ''),
    ('Investment Withdrawals', 'Income', '', ''),
    ('Other Income', 'Income', '', ''),
]

def build_categories():
    # render_tiers.py matches the Tier cell against the regex Tier\s*(\d) -- it needs the literal
    # word "Tier", not a bare digit.
    rows = [['Category', 'Group', 'Type', 'Bucket', 'Tier']]
    for name, typ, bucket, tier in CATEGORIES:
        rows.append([name, '', typ, bucket, f'Tier {tier}' if tier else ''])
    return rows

# ── Transactions ───────────────────────────────────────────────────────────────────────────────
ACCOUNTS = {
    'checking': 'Household Checking',
    'brokerage': 'Brokerage',
    'ira': 'Retirement IRA',
}

def build_transactions():
    rows = [['Date', 'Description', 'Category', 'Amount', 'Account', 'Tags']]

    for i, m in enumerate(MONTHS):
        yr = i // 12   # 0, 1, or 2 -- which of the three 12-month windows this month falls in

        # ── guaranteed income ──
        rows.append([fmt(month_day(m, 3)), 'Alex Social Security', 'Alex Social Security',
                     f'{jitter(1950, .01):.2f}', ACCOUNTS['checking'], ''])
        rows.append([fmt(month_day(m, 3)), 'Alex Pension', 'Alex Pension',
                     f'{jitter(2700, .01):.2f}', ACCOUNTS['checking'], ''])
        rows.append([fmt(month_day(m, 5)), 'Jordan Pension', 'Jordan Pension',
                     f'{jitter(1900, .01):.2f}', ACCOUNTS['checking'], ''])
        if i >= 13:   # Jordan starts claiming Social Security partway through the window --
                      # exercises the "X started" detection in render_dashboard.py
            rows.append([fmt(month_day(m, 5)), 'Jordan Social Security', 'Jordan Social Security',
                         f'{jitter(1600, .01):.2f}', ACCOUNTS['checking'], ''])
        if random.random() < 0.4:
            rows.append([fmt(month_day(m)), 'Dividends', 'Other Income',
                         f'{round(random.uniform(40, 160), 2):.2f}', ACCOUNTS['brokerage'], ''])

        # ── portfolio draw: a steady routine amount most months, occasional larger episodic
        #    (tax-funding) draws -- keeps a single clean mode for the "routine rate" detection ──
        rows.append([fmt(month_day(m, 1)), 'Scheduled withdrawal', 'Investment Withdrawals',
                     '9000.00', ACCOUNTS['brokerage'], ''])
        if m.month in (1, 4) and random.random() < 0.7:
            rows.append([fmt(month_day(m, 15)), 'Tax withdrawal', 'Investment Withdrawals',
                         f'{round(random.uniform(6000, 9000), 2):.2f}', ACCOUNTS['brokerage'], ''])

        # ── steady lifestyle categories (present every month, mild variance) ──
        steady = [
            ('Mortgage', 3214.00, 0.0),
            ('Property Tax', 1040.00, 0.03),
            ('Utilities', 380.00, 0.18),
            ('Groceries', 920.00, 0.15),
            ('Healthcare', 410.00, 0.25),
            ('Auto & Gas', 310.00, 0.20),
            ('Dining Out', 480.00, 0.22),
            ('Subscriptions', 138.00, 0.05),
            ('Personal Care', 210.00, 0.18),
            ('Investment Fees', 145.00, 0.08),
        ]
        for cat, base, pct in steady:
            amt = base if pct == 0 else jitter(base, pct)
            rows.append([fmt(month_day(m)), cat, cat, f'{amt:.2f}', ACCOUNTS['checking'], ''])

        # ── Home Maintenance: baseline, with a roof-repair spike concentrated in the MIDDLE
        #    year -- exercises the "middle year's peak" detection in render_dashboard.py ──
        base = 260.00
        if yr == 1 and m.month in (4, 5, 6):
            base += random.uniform(2600, 4200)   # the roof
        rows.append([fmt(month_day(m)), 'Home Maintenance', 'Home Maintenance',
                     f'{jitter(base, .1):.2f}', ACCOUNTS['checking'], ''])

        # ── Shopping: the blank-Tier category, steady-ish spend ──
        rows.append([fmt(month_day(m)), 'Shopping', 'Shopping',
                     f'{jitter(330, .3):.2f}', ACCOUNTS['checking'], ''])

        # ── Travel: naturally lumpy, a big trip most years but not every month ──
        if random.random() < 0.35:
            rows.append([fmt(month_day(m)), 'Travel', 'Travel',
                         f'{round(random.uniform(1200, 5200), 2):.2f}', ACCOUNTS['checking'], ''])

        # ── Home Improvements: near-zero for two years, a kitchen remodel concentrated in the
        #    THIRD (most recent) year -- a clean "volatile"/"new" trigger ──
        if yr == 2 and m.month in (2, 3, 4, 5):
            rows.append([fmt(month_day(m)), 'Kitchen remodel', 'Home Improvements',
                         f'{round(random.uniform(4500, 9000), 2):.2f}', ACCOUNTS['checking'], ''])
        elif random.random() < 0.08:
            rows.append([fmt(month_day(m)), 'Home Improvements', 'Home Improvements',
                         f'{round(random.uniform(150, 500), 2):.2f}', ACCOUNTS['checking'], ''])

        # ── Education: doesn't exist until the most recent year -- a clean "new" trigger ──
        if yr == 2 and random.random() < 0.5:
            rows.append([fmt(month_day(m)), 'Tuition help', 'Education',
                         f'{round(random.uniform(400, 1400), 2):.2f}', ACCOUNTS['checking'], ''])

        # ── Giving: steady baseline ──
        rows.append([fmt(month_day(m)), 'Giving', 'Giving',
                     f'{jitter(420, .15):.2f}', ACCOUNTS['checking'], ''])

        # ── Gifts: mostly quiet, one big wedding-year spike -- another clean "volatile" trigger ──
        if yr == 0 and m.month in (6, 7):
            rows.append([fmt(month_day(m)), 'Wedding gift', 'Gifts',
                         f'{round(random.uniform(1800, 3200), 2):.2f}', ACCOUNTS['checking'], ''])
        elif random.random() < 0.15:
            rows.append([fmt(month_day(m)), 'Gift', 'Gifts',
                         f'{round(random.uniform(60, 250), 2):.2f}', ACCOUNTS['checking'], ''])

        # ── taxes: quarterly estimates, with a January payment tagged ty<prior year> to
        #    demonstrate the tag-based liability-year reassignment feature ──
        if m.month in (1, 4, 6, 9):
            tag = f'ty{m.year - 1}' if m.month == 1 else ''
            rows.append([fmt(month_day(m, 15)), 'IRS estimated payment', 'IRS',
                         f'{round(random.uniform(3800, 7200), 2):.2f}', ACCOUNTS['checking'], tag])
            rows.append([fmt(month_day(m, 15)), 'CA FTB estimated payment', 'CA Tax',
                         f'{round(random.uniform(1200, 2600), 2):.2f}', ACCOUNTS['checking'], tag])

    # Tiller convention: money OUT (expenses) is negative, money IN (income) is positive. Every
    # row above was built with a plain positive magnitude for readability -- flip sign here for
    # anything that isn't one of the income categories, in one place, rather than at every call
    # site above where it'd be easy to miss one.
    income_cats = {'Alex Social Security', 'Jordan Social Security', 'Alex Pension', 'Jordan Pension',
                   'Investment Withdrawals', 'Other Income'}
    for row in rows[1:]:
        if row[2] not in income_cats:
            row[3] = f'{-float(row[3]):.2f}'

    return rows

# ── Balance History ────────────────────────────────────────────────────────────────────────────
def build_balance_history():
    rows = [['Date', 'Account', 'Balance', 'Class', 'Account Status']]
    brokerage, ira, mortgage = 1_180_000.0, 860_000.0, 418_000.0
    checking = 22_000.0
    for m in MONTHS:
        asof = addmonths(m, 1) - timedelta(days=1)
        brokerage *= 1 + random.uniform(-0.025, 0.032)
        ira *= 1 + random.uniform(-0.025, 0.032)
        mortgage = max(0, mortgage - random.uniform(700, 950))
        checking = max(8000, checking + random.uniform(-3000, 3200))
        rows.append([fmt(asof), ACCOUNTS['checking'], f'{checking:.2f}', 'asset', 'ACTIVE'])
        rows.append([fmt(asof), ACCOUNTS['brokerage'], f'{brokerage:.2f}', 'asset', 'ACTIVE'])
        rows.append([fmt(asof), ACCOUNTS['ira'], f'{ira:.2f}', 'asset', 'ACTIVE'])
        rows.append([fmt(asof), 'Mortgage', f'{mortgage:.2f}', 'liability', 'ACTIVE'])
    # one dormant account, present in Balance History but excluded from net worth via the
    # Accounts-tab registry below -- exercises that filter path
    rows.append([fmt(START_MONTH), 'Old 401k (rolled over)', '4200.00', 'asset', 'CLOSED'])
    return rows

# ── Accounts ───────────────────────────────────────────────────────────────────────────────────
def build_accounts():
    # Column index 7 (the 8th column) is the "include in net worth" registry render_dashboard.py
    # and render_advisor_pdf.py read -- a value there must exactly match the account name used in
    # Balance History. Left blank for the dormant account so it's excluded. This positional
    # convention mirrors the private original; consider it a known quirk, not a design choice.
    header = ['Account', 'Institution', 'Type', 'C4', 'C5', 'C6', 'C7', 'On Balance Sheet']
    rows = [header]
    for name in (ACCOUNTS['checking'], ACCOUNTS['brokerage'], ACCOUNTS['ira'], 'Mortgage'):
        rows.append([name, 'Demo Bank', '', '', '', '', '', name])
    rows.append(['Old 401k (rolled over)', 'Former Employer', '', '', '', '', '', ''])
    return rows

# ── Downsize Model ─────────────────────────────────────────────────────────────────────────────
def build_downsize_model():
    rows = [
        ['Assumption', 'Value', 'Note'],
        ['Current market value', '1250000', 'Illustrative demo figure, not a real appraisal.'],
        ['Original purchase price', '480000', ''],
        ['Capital improvements (basis add)', '95000', ''],
        ['Remaining mortgage balance', '400000', 'Should roughly match the live Balance History figure.'],
        ['SD documentary transfer tax rate', '0.0011', ''],
        ['Agent + closing costs rate', '0.06', ''],
        ['Federal LTCG rate', '0.15', 'Illustrative example rate, not tax advice.'],
        ['NIIT surtax', '0.038', ''],
        ['CA capital gains rate', '0.093', 'CA taxes capital gains as ordinary income.'],
        ['Section 121 exclusion (MFJ)', '500000', ''],
        ['General inflation', '0.03', ''],
        ['Rent inflation', '0.03', ''],
        ['Expected real return (after-tax)', '0.05', ''],
        ['Return volatility (annual, real)', '0.12', ''],
        ['Monte Carlo trials', '3000', 'Kept low so the demo renders in seconds.'],
        ['Primary horizon (years)', '25', ''],
        ['', '', ''],
        ['Homeownership tax benefit (lost when you sell)', '', ''],
        ['Federal marginal ordinary rate', '0.22', 'Illustrative example rate, not tax advice.'],
        ['CA marginal ordinary rate', '0.093', 'Illustrative example rate, not tax advice.'],
        ['Charitable giving, deductible (annual)', '5000', ''],
        ['Other itemized deductions excl. home (state tax + personal property)', '3200', ''],
        ['Federal standard deduction (MFJ)', '30000', 'Illustrative — check the current-year figure.'],
        ['Federal SALT cap', '40000', 'Illustrative — check the current-year figure.'],
    ]
    return rows

# ── Portfolio Model ────────────────────────────────────────────────────────────────────────────
def build_portfolio_model():
    rows = [
        ['Assumption', 'Value', 'Note'],
        ['Expected real return (after-tax)', '0.05', ''],
        ['Return volatility (annual, real)', '0.12', ''],
        ['Monte Carlo trials', '3000', 'Kept low so the demo renders in seconds.'],
        ['Primary horizon (years)', '25', ''],
        ['General inflation', '0.03', ''],
        ['Lifestyle spending real growth', '0.0', ''],
        ['Guaranteed income real growth', '0.0', 'Assumes both pensions are fully COLA\'d.'],
    ]
    return rows

TABS = {
    'Categories': build_categories,
    'Transactions': build_transactions,
    'Balance History': build_balance_history,
    'Accounts': build_accounts,
    'Downsize Model': build_downsize_model,
    'Portfolio Model': build_portfolio_model,
}

def connect():
    creds = Credentials.from_service_account_file(glob.glob('./*.json')[0],
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    return gspread.authorize(creds).open(SHEET_NAME)

def main():
    dry_run = '--dry-run' in sys.argv
    data = {tab: fn() for tab, fn in TABS.items()}
    for tab, rows in data.items():
        print(f'  {tab}: {len(rows) - 1} rows')

    if dry_run:
        print('dry run only, nothing written')
        return

    book = connect()
    for tab, rows in data.items():
        try:
            ws = book.worksheet(tab)
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws = book.add_worksheet(title=tab, rows=len(rows) + 10, cols=len(rows[0]) + 2)
        ws.update(values=rows, range_name='A1')
        print(f'wrote {tab} ({len(rows) - 1} rows)')

if __name__ == '__main__':
    main()
