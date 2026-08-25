#!/usr/bin/env python3
"""
render_downsize.py — regenerate reports/downsize.html: does selling the house and renting
instead make sense, and what rent could the net proceeds sustainably support?

All assumptions live on the Categories-tab-style config surface, the "Downsize Model" tab —
market value, basis, tax rates, expected return, volatility, everything — so changing any of
them is a cell edit, not a code change, same pattern as Bucket/Tier and AutoCat. Only two things
are pulled live from elsewhere: the current mortgage payoff (Balance History, cross-checked
against the sheet's own assumption) and the current cost of owning the house (the "Housing"
bucket total, same figure already shown on dashboard.html/downsize's counterfactual comparison).

Two deliberate scope decisions, both made with the user 2026-08-03:
  * REAL terms throughout. Expected return, volatility and the withdrawal itself are all
    inflation-adjusted; nothing here is nominal. The withdrawal's own real growth rate is derived
    from "General inflation" vs "Rent inflation" on the sheet -- not assumed to be flat. With the
    two set equal (the sheet default, 3%/3%), that derived rate is exactly 0% and the simulation
    withdraws a constant real amount each year; set "Rent inflation" higher (rents historically
    outpacing general prices is plausible for San Diego specifically) and the simulated withdrawal
    escalates in real terms year over year, which lowers the sustainable starting amount at every
    confidence level. Both input rates and the derived rate are shown on the page (added
    2026-08-03 -- they were being read from the sheet but silently unused, which would have made
    editing "Rent inflation" a no-op cell, breaking this project's config-surface convention).
  * Proceeds-only. This models the ~$1.4M (or whatever the sheet currently says) of net sale
    proceeds as their own pot, not the whole portfolio -- it answers "does selling the house
    help," not full retirement sustainability (that's the separate, bigger Tier 3 roadmap item).
    The simulation engine below is horizon-agnostic and trial-count-agnostic by construction, so
    it is the natural thing to reuse if/when that bigger question gets built.

The Monte Carlo answers "how much rent, at what confidence" rather than picking one confidence
level for you: for a grid of candidate withdrawal amounts and a handful of horizons (20/25/30,
plus 50 as a practical stand-in for "indefinitely"), it reports the fraction of `trials`
simulated paths that never hit zero. Ruin is permanent -- once a path hits zero it stays there,
it cannot mathematically "recover" on a later good year.

Tax rates (Federal LTCG, marginal ordinary rates, standard deduction, SALT cap, etc.) all live on
the "Downsize Model" sheet tab as illustrative example figures, not tax advice -- check them
against your own actual return each year rather than trusting them indefinitely. Also included:
the tax BENEFIT of owning (mortgage interest + property tax deductions, lost the moment the house
is sold) is netted against the raw cash cost of owning before comparing to sustainable rent --
computed live each run from the current 12-month window's actual mortgage interest and property
tax, using the rates and "other itemized deductions" baseline from that sheet tab. NOT A CPA
FIGURE -- built for a CPA to review, not to replace one; see the "Homeownership tax benefit"
section on the "Downsize Model" sheet tab and the page's own "Tax benefit of owning" section for
the full derivation and every simplifying assumption made along the way.

  python3 render_downsize.py
"""
import gspread, glob, os, re, html, sys
import numpy as np
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROJECT_ROOT, SHEET_ID, SHEET_NAME, HOUSEHOLD_NAME

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/downsize.html'

HORIZONS = (20, 25, 30, 50)   # 50yr stands in for "indefinitely" / maintain-principal
WGRID_STEPS = 60              # candidate withdrawal amounts to test per horizon

def pdate(s):
    for f in ('%m/%d/%Y', '%m/%d/%y'):
        try: return datetime.strptime((s or '').strip(), f).date()
        except ValueError: pass
    return None
def money(s):
    s = (s or '').strip()
    if not s: return None
    neg = s.startswith('-') or s.startswith('(')
    v = re.sub(r'[^0-9.]', '', s)
    return (-float(v) if neg else float(v)) if v else None
def usd(n, dp=0): return f'${n:,.{dp}f}'
def k(n): return f'${n/1000:,.0f}k'
def monthstart(d): return date(d.year, d.month, 1)
def addmonths(d, n):
    y, m = d.year, d.month + n
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return date(y, m, 1)

def connect():
    creds = Credentials.from_service_account_file(glob.glob('./*.json')[0],
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return gspread.authorize(creds).open_by_key(SHEET_ID)

def read_assumptions(book):
    # This page is optional: it needs the custom "Downsize Model" tab, which a stock Tiller sheet
    # doesn't have. Exit 3 (not a traceback) so run_demo.sh can skip it with a readable message
    # rather than aborting the whole run -- the three reports needing no custom tabs still run.
    try:
        vals = book.worksheet('Downsize Model').get_all_values()
    except gspread.WorksheetNotFound:
        print('No "Downsize Model" tab on this sheet — skipping the Downsize report.\n'
              '  This page is optional. To add it, see "Downsize Model" in SETUP_GOOGLE_SHEETS.md.')
        sys.exit(3)
    a = {}
    for r in vals:
        if len(r) < 2 or not r[0].strip() or not r[1].strip(): continue
        try: a[r[0].strip()] = float(r[1].replace(',', ''))
        except ValueError: continue
    return a

# ── Optional-page availability ────────────────────────────────────────────────────────────────
# The Downsize and Retirement Sustainability pages each need a custom tab that a stock Tiller
# sheet doesn't have, so a partial run is normal and expected. Every page therefore checks which
# of those tabs exist and (a) renders nav entries for missing pages as plain text rather than
# dead links, and (b) says so explicitly, so a partial set of reports can't be mistaken for the
# whole thing. Duplicated in each render_*.py on purpose -- see README on why these don't share.
OPTIONAL_PAGES = (('Downsize Model', 'downsize.html', 'Downsize Model'),
                  ('Portfolio Model', 'sustainability.html', 'Retirement Sustainability'))

def tab_titles(book):
    return {w.title for w in book.worksheets()}

def nav_html(here, titles):
    items = [('index.html', 'Overview'), ('dashboard.html', 'Financial Summary'),
             ('spending.html', 'Spending by Category'), ('tiers.html', 'Spending by Tier'),
             ('downsize.html', 'Downsize Model'),
             ('sustainability.html', 'Retirement Sustainability')]
    need = {href: tab for tab, href, _ in OPTIONAL_PAGES}
    out = []
    for href, label in items:
        tab = need.get(href)
        if tab and tab not in titles:
            out.append(f'<span class="na" title="Not generated \u2014 add the &quot;{tab}&quot; tab '
                       f'to your sheet to enable this report">{label}</span>')
        elif href == here:
            out.append(f'<a href="{href}" class="here">{label}</a>')
        else:
            out.append(f'<a href="{href}">{label}</a>')
    return '\n      '.join(out)

def missing_note(titles):
    missing = [(tab, label) for tab, _, label in OPTIONAL_PAGES if tab not in titles]
    if not missing:
        return ''
    which = ' and '.join(f'<b>{label}</b> (needs a "{tab}" tab)' for tab, label in missing)
    return ('<div class="partial">This is a partial set of reports: ' + which +
            ' could not be generated, and the advisor PDF is skipped whenever either model is '
            'missing. Everything shown here is complete and correct on its own \u2014 see '
            'SETUP_GOOGLE_SHEETS.md to add the missing tab.</div>')

def compute():
    book = connect()
    titles = tab_titles(book)
    a = read_assumptions(book)

    home_value = a['Current market value']
    purchase_price = a['Original purchase price']
    improvements = a['Capital improvements (basis add)']
    mortgage_assumed = a['Remaining mortgage balance']
    transfer_tax_rate = a['SD documentary transfer tax rate']
    closing_rate = a['Agent + closing costs rate']
    fed_ltcg = a['Federal LTCG rate']
    niit = a['NIIT surtax']
    ca_rate = a['CA capital gains rate']
    exclusion = a['Section 121 exclusion (MFJ)']
    inflation = a['General inflation']
    rent_inflation = a['Rent inflation']
    mean_return = a['Expected real return (after-tax)']
    vol = a['Return volatility (annual, real)']
    trials = int(a['Monte Carlo trials'])
    primary_horizon = int(a['Primary horizon (years)'])

    # live cross-check on the mortgage payoff assumption -- same Balance History logic as
    # render_dashboard.py's mortgage note, kept read-only here (never overwrites the assumption)
    bh = book.worksheet('Balance History').get_all_values(); bhd = bh[0]
    bD, bA, bB = bhd.index('Date'), bhd.index('Account'), bhd.index('Balance')
    bC = bhd.index('Class') if 'Class' in bhd else None
    obs = defaultdict(list); cls = {}
    for r in bh[1:]:
        if len(r) <= bB: continue
        d = pdate(r[bD]); b = money(r[bB]); acct = r[bA].strip()
        if d is None or b is None or not acct: continue
        obs[acct].append((d, b))
        if bC is not None and len(r) > bC: cls[acct] = r[bC].strip().lower()
    for acct in obs: obs[acct].sort()
    loans = [acct for acct in cls if 'mortgage' in acct.lower() or 'loan' in acct.lower()]
    mortgage_live = None
    if loans:
        pts = obs.get(loans[0], [])
        if pts: mortgage_live = pts[-1][1]

    # current cost of owning the house -- the "Housing" bucket, current 12mo window, same
    # pattern (and same live figure) as render_dashboard.py / render_advisor_pdf.py
    cats = book.worksheet('Categories').get_all_values(); chdr = cats[0]
    ctype = {r[0].strip(): (r[2].strip() if len(r) > 2 else '') for r in cats[1:] if r and r[0].strip()}
    bcol = chdr.index('Bucket') if 'Bucket' in chdr else None
    cbucket = {r[0].strip(): (r[bcol].strip() if bcol is not None and len(r) > bcol else '')
               for r in cats[1:] if r and r[0].strip()}
    tvals = book.worksheet('Transactions').get_all_values(); thdr = tvals[0]
    ci = lambda n: thdr.index(n)
    D, CA, AM = ci('Date'), ci('Category'), ci('Amount')
    today = date.today()
    end = monthstart(today) - timedelta(days=1)
    start = addmonths(monthstart(end), -11)
    housing_cost = mortgage_paid = property_tax_paid = 0.0
    for r in tvals[1:]:
        if len(r) <= AM: continue
        d = pdate(r[D]); amt = money(r[AM])
        if d is None or amt is None or not (start <= d <= end): continue
        c = r[CA].strip()
        if ctype.get(c, '') == 'Expense' and cbucket.get(c, '') == 'Housing':
            housing_cost += -amt
        if c == 'Mortgage': mortgage_paid += -amt
        if c == 'Property Tax': property_tax_paid += -amt

    # mortgage interest this window -- same principle as mortgage_split.py (only principal moves
    # the loan balance, interest doesn't), applied over this model's actual current window rather
    # than a fixed lookback; total paid comes from real Mortgage-category transactions above,
    # not an estimated payment-count, so it stays exact even in a month the feed missed a payment
    mortgage_interest = 0.0
    if loans:
        pts = obs.get(loans[0], [])
        start_bal = [b for d, b in pts if d <= start]
        end_bal = [b for d, b in pts if d <= end]
        if start_bal and end_bal:
            principal_reduction = start_bal[-1] - end_bal[-1]
            mortgage_interest = max(0.0, mortgage_paid - principal_reduction)

    # Tax benefit of owning (mortgage interest + property tax deductions), lost the moment you
    # sell -- renting has no comparable deduction. NOT A CPA FIGURE -- several simplifying
    # assumptions below, each documented; for CPA review, not a substitute for one.
    #
    # Federal: the naive "mortgage interest + property tax, times your marginal rate" number
    # OVERSTATES the benefit, because it ignores the standard deduction. If a household's
    # remaining itemized deductions (charity + other SALT) fall under the standard deduction once
    # the home is out of the picture, they'd take the standard deduction instead of itemizing at
    # all. The TRUE marginal benefit is only the portion of itemizing WITH the home that exceeds
    # what they'd get anyway (the greater of the standard deduction or itemizing without the
    # home), not the full deduction amount -- this can roughly halve the naive estimate depending
    # on the household's other itemized deductions.
    #
    # California: skipped the same crowd-out logic on purpose. CA's standard deduction is
    # typically low enough relative to itemized deductions that itemizing on the CA return stays
    # close to certain regardless of the home, so the naive calculation (deduction x CA marginal
    # rate) matches the precise one closely enough not to be worth the extra complexity. CA also
    # does not apply the federal SALT cap at all -- property tax is separately, fully deductible
    # on the CA return.
    other_itemized_excl_home = a['Charitable giving, deductible (annual)'] + a['Other itemized deductions excl. home (state tax + personal property)']
    federal_std_ded = a['Federal standard deduction (MFJ)']
    federal_salt_cap = a['Federal SALT cap']
    federal_marginal = a['Federal marginal ordinary rate']
    ca_marginal = a['CA marginal ordinary rate']

    federal_baseline_deduction = max(federal_std_ded, other_itemized_excl_home)
    federal_salt_with_home = min(property_tax_paid + a['Other itemized deductions excl. home (state tax + personal property)'], federal_salt_cap)
    federal_itemized_with_home = federal_salt_with_home + mortgage_interest + a['Charitable giving, deductible (annual)']
    federal_incremental_deduction = max(0.0, federal_itemized_with_home - federal_baseline_deduction)
    federal_ownership_tax_benefit = federal_incremental_deduction * federal_marginal

    ca_ownership_tax_benefit = (property_tax_paid + mortgage_interest) * ca_marginal

    ownership_tax_benefit = federal_ownership_tax_benefit + ca_ownership_tax_benefit
    effective_housing_cost = max(0.0, housing_cost - ownership_tax_benefit)

    # deterministic net-proceeds waterfall
    selling_costs = home_value * (transfer_tax_rate + closing_rate)
    basis = purchase_price + improvements
    realized_gain = max(0, home_value - selling_costs - basis)
    taxable_gain = max(0, realized_gain - exclusion)
    tax = taxable_gain * (fed_ltcg + niit + ca_rate)
    net_proceeds = home_value - selling_costs - mortgage_assumed - tax

    # Real (inflation-adjusted) annual growth rate of the rent target itself, derived from the
    # two inflation assumptions above -- NOT assumed to be zero. If general inflation and rent
    # inflation are set equal (the default), this is exactly 0%: rent keeps pace with prices
    # exactly, so a flat real withdrawal is correct and nothing further is needed. If someone
    # sets rent inflation higher than general inflation on the sheet (rents outpacing overall
    # prices, plausible for San Diego specifically), this comes out positive and the simulated
    # withdrawal escalates in real terms year over year -- otherwise that sheet cell would be a
    # silent no-op, which would violate this project's whole "editing a cell changes behavior"
    # config-surface convention.
    real_rent_growth = (1 + rent_inflation) / (1 + inflation) - 1

    # Monte Carlo: real terms, ruin is permanent
    rng = np.random.default_rng(20260803)
    max_h = max(HORIZONS)
    returns = rng.normal(mean_return, vol, size=(trials, max_h))

    def success_curve(horizon):
        r = returns[:, :horizon]
        wmax = net_proceeds * mean_return * 2.2   # generous upper bound for the grid
        grid = np.linspace(0, wmax, WGRID_STEPS)
        growth = (1 + real_rent_growth) ** np.arange(horizon)   # year-1 withdrawal scaled by this each year
        curve = []
        for w in grid:
            bal = np.full(trials, net_proceeds)
            ruined = np.zeros(trials, dtype=bool)
            for yr in range(horizon):
                bal = np.where(ruined, 0.0, bal * (1 + r[:, yr]) - w * growth[yr])
                ruined |= bal <= 0
            success = 1.0 - ruined.mean()
            curve.append((w, success))
        return curve

    curves = {h: success_curve(h) for h in HORIZONS}

    def withdrawal_at(horizon, target):
        curve = curves[horizon]
        # curve is monotonically non-increasing in w; find the largest w still >= target
        best = 0.0
        for w, s in curve:
            if s >= target: best = w
            else: break
        return best

    headline = {h: {p: withdrawal_at(h, p) for p in (0.75, 0.90, 0.95)} for h in HORIZONS}

    return dict(titles=titles, a=a, home_value=home_value, selling_costs=selling_costs, basis=basis,
                realized_gain=realized_gain, taxable_gain=taxable_gain, tax=tax,
                mortgage_assumed=mortgage_assumed, mortgage_live=mortgage_live,
                net_proceeds=net_proceeds, housing_cost=housing_cost,
                mortgage_interest=mortgage_interest, property_tax_paid=property_tax_paid,
                other_itemized_excl_home=other_itemized_excl_home,
                federal_baseline_deduction=federal_baseline_deduction,
                federal_itemized_with_home=federal_itemized_with_home,
                federal_incremental_deduction=federal_incremental_deduction,
                federal_ownership_tax_benefit=federal_ownership_tax_benefit,
                ca_ownership_tax_benefit=ca_ownership_tax_benefit,
                ownership_tax_benefit=ownership_tax_benefit,
                effective_housing_cost=effective_housing_cost,
                inflation=inflation, rent_inflation=rent_inflation,
                real_rent_growth=real_rent_growth,
                mean_return=mean_return, vol=vol, trials=trials,
                primary_horizon=primary_horizon, curves=curves, headline=headline,
                generated=datetime.now())

def build_html(f):
    nav = nav_html('downsize.html', f['titles']); partial_note = missing_note(f['titles'])
    a = f['a']; net_proceeds = f['net_proceeds']; headline = f['headline']
    ph = f['primary_horizon']
    mortgage_note = ''
    if f['mortgage_live'] is not None:
        diff = f['mortgage_assumed'] - f['mortgage_live']
        mortgage_note = (f' (live-tracked payoff is currently {usd(f["mortgage_live"])}, '
                          f'{"close to" if abs(diff) < 10000 else ("above" if diff < 0 else "below")} '
                          f'the {usd(f["mortgage_assumed"])} assumption)')

    waterfall_rows = [
        ('Current market value', f['home_value']),
        ('− Transfer tax + agent/closing costs', -f['selling_costs']),
        ('− Mortgage payoff', -f['mortgage_assumed']),
        ('− Capital gains tax (fed + NIIT + CA)', -f['tax']),
        ('= Net proceeds to invest', net_proceeds),
    ]
    waterfall_html = ''.join(
        f'<div class="row{" tot" if name.startswith("=") else ""}"><span>{html.escape(name)}</span>'
        f'<span class="v num">{"−" if v < 0 else ""}{usd(abs(v))}</span></div>' for name, v in waterfall_rows)

    housing_cost = f['housing_cost']
    housing_cost_mo = housing_cost / 12
    effective_housing_cost = f['effective_housing_cost']
    effective_housing_cost_mo = effective_housing_cost / 12
    ownership_tax_benefit = f['ownership_tax_benefit']

    inflation = f['inflation']; rent_inflation = f['rent_inflation']
    real_rent_growth = f['real_rent_growth']
    if abs(real_rent_growth) < 0.0005:
        inflation_note = (f'General inflation and rent inflation are both assumed at '
                           f'{inflation*100:.0f}%/yr, so rent is assumed to track general prices '
                           f'exactly — the withdrawal below is a flat real (inflation-adjusted) '
                           f'amount, it does not need to grow on its own.')
    else:
        direction = 'outpacing' if real_rent_growth > 0 else 'undershooting'
        inflation_note = (f'General inflation is assumed at {inflation*100:.1f}%/yr and rent '
                           f'inflation at {rent_inflation*100:.1f}%/yr — rent {direction} general '
                           f'prices by {abs(real_rent_growth)*100:.1f}%/yr in real terms, so the '
                           f'simulated withdrawal grows at that rate each year; the table below '
                           f'shows the starting (year-1) monthly amount, not a flat figure.')

    # figures below are converted to monthly for display only -- the model itself simulates in
    # real annual dollars (matching the annual return/volatility inputs), monthly is just easier
    # to compare against a rent listing
    curve_js = ','.join(
        f'[{h},[{",".join(f"[{w/12:.0f},{s:.4f}]" for w, s in f["curves"][h])}]]' for h in (20, 25, 30, 50))

    headline_rows = ''.join(
        f'<tr><td>{h}{" (indefinite proxy)" if h == 50 else ""}</td>'
        f'<td class="num">{usd(headline[h][0.75]/12)}</td>'
        f'<td class="num">{usd(headline[h][0.90]/12)}</td>'
        f'<td class="num">{usd(headline[h][0.95]/12)}</td></tr>'
        for h in (20, 25, 30, 50))

    ph_90 = headline[ph][0.90]
    ph_90_mo = ph_90 / 12
    delta_mo = abs(ph_90_mo - effective_housing_cost_mo)
    equivalent = abs(ph_90 - effective_housing_cost) / effective_housing_cost <= 0.05 if effective_housing_cost else False
    if equivalent:
        cmp_note = (f'Invested, that could sustainably support about <b>{usd(ph_90_mo)}/mo</b> in rent at '
                    f'90% confidence over {ph} years — essentially equivalent to the '
                    f'{usd(effective_housing_cost_mo)}/mo it <i>effectively</i> costs to own our house today '
                    f'(within {usd(delta_mo)}/mo), after netting out the mortgage-interest and property-tax '
                    f'tax benefit — see below.')
    else:
        cmp_note = (f'Invested, that could sustainably support about <b>{usd(ph_90_mo)}/mo</b> in rent at '
                    f'90% confidence over {ph} years — {"more" if ph_90 > effective_housing_cost else "less"} '
                    f'than the {usd(effective_housing_cost_mo)}/mo it <i>effectively</i> costs to own our '
                    f'house today, after netting out the mortgage-interest and property-tax tax benefit '
                    f'(see below) — {"freeing up" if ph_90 > effective_housing_cost else "costing"} roughly '
                    f'{usd(delta_mo)}/mo.')

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Downsize / Sale-to-Rent Model — {html.escape(HOUSEHOLD_NAME)}</title>
<style>
:root{{--paper:#e9ece8;--card:#fff;--edge:#d5dbd4;--ink:#1a2420;--muted:#5b6a62;--faint:#8a988f;
 --gold:#8f6f34;--green:#2f6d54;--clay:#a9503b;--slate:#4a6572;--grid:#e4e8e3;--hero:#16211d;
 --shadow:0 1px 2px rgba(20,33,28,.06),0 12px 30px rgba(20,33,28,.07);}}
@media (prefers-color-scheme:dark){{:root{{--paper:#0e1512;--card:#141d18;--edge:#243029;--ink:#e7ede8;
 --muted:#9fb0a6;--faint:#6d7f75;--gold:#cba85e;--green:#5fa588;--clay:#d0745c;--slate:#8aa6b3;
 --grid:#202b25;--hero:#0b110e;--shadow:0 1px 2px rgba(0,0,0,.4),0 14px 34px rgba(0,0,0,.36);}}}}
:root[data-theme="light"]{{--paper:#e9ece8;--card:#fff;--edge:#d5dbd4;--ink:#1a2420;--muted:#5b6a62;
 --faint:#8a988f;--gold:#8f6f34;--green:#2f6d54;--clay:#a9503b;--slate:#4a6572;--grid:#e4e8e3;--hero:#16211d;}}
:root[data-theme="dark"]{{--paper:#0e1512;--card:#141d18;--edge:#243029;--ink:#e7ede8;--muted:#9fb0a6;
 --faint:#6d7f75;--gold:#cba85e;--green:#5fa588;--clay:#d0745c;--slate:#8aa6b3;--grid:#202b25;--hero:#0b110e;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);line-height:1.5;
 font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 64px}}
.serif{{font-family:"Iowan Old Style",Georgia,"Times New Roman",serif}}
.num{{font-variant-numeric:tabular-nums}}
.eyebrow{{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);font-weight:600}}
h1{{font-size:1.45rem;margin:.15em 0 0;font-weight:600;letter-spacing:-.01em}}
header .meta{{color:var(--muted);font-size:.85rem;margin-top:6px}}
.rule{{height:1px;background:var(--edge);margin:26px 0}}
.topnav{{display:flex;gap:8px;margin:16px 0 0;flex-wrap:wrap}}
.topnav a{{font-size:.78rem;text-decoration:none;color:var(--muted);border:1px solid var(--edge);
 border-radius:999px;padding:5px 13px;background:var(--card);transition:.15s}}
.topnav a:hover,.topnav a:focus-visible{{color:var(--ink);border-color:var(--gold)}}
.topnav a.here{{background:var(--hero);color:#eef3ef;border-color:var(--hero)}}
.trend{{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:22px 24px;
 box-shadow:var(--shadow);margin-top:20px}}
.trend h2{{font-size:1.02rem;font-weight:600;margin:0 0 3px}}
.trend .period{{color:var(--faint);font-size:.8rem;margin:5px 0 0;max-width:70ch;line-height:1.45}}
.trend .period b{{color:var(--muted)}}
.trend .lead{{color:var(--ink);font-size:.95rem;margin:12px 0 4px;max-width:66ch;line-height:1.5}}
.trend .lead b{{color:var(--green)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px}}
.mini{{background:var(--paper);border:1px solid var(--edge);border-radius:12px;padding:16px 18px}}
.mini h3{{font-size:.82rem;font-weight:600;margin:0 0 10px;color:var(--muted)}}
.mini .row{{display:flex;justify-content:space-between;gap:10px;font-size:.86rem;padding:5px 0}}
.mini .row .v{{color:var(--muted)}}
.mini .row.tot{{border-top:1px solid var(--edge);margin-top:5px;padding-top:9px;font-weight:600}}
.mini .row.tot .v{{color:var(--ink)}}
.htable{{width:100%;border-collapse:collapse;font-size:.86rem;margin-top:10px}}
.htable th{{text-align:right;font-size:.68rem;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);
 font-weight:600;padding:0 0 8px 12px;border-bottom:1px solid var(--edge)}}
.htable th:first-child{{text-align:left;padding-left:0}}
.htable td{{padding:9px 0 9px 12px;border-bottom:1px solid var(--edge);text-align:right}}
.htable td:first-child{{text-align:left;padding-left:0;color:var(--muted)}}
.htable tr:last-child td{{border-bottom:none}}
svg{{display:block;width:100%;height:auto}}
.legend{{display:flex;gap:18px;font-size:.76rem;color:var(--muted);margin-top:10px;flex-wrap:wrap}}
.legend span{{display:inline-flex;align-items:center;gap:6px}}
.legend i{{width:9px;height:9px;border-radius:2px;display:inline-block}}
.note{{background:var(--card);border:1px solid var(--edge);border-left:3px solid var(--slate);
 border-radius:12px;padding:18px 22px;margin-top:14px;box-shadow:var(--shadow)}}
.note h2{{font-size:.98rem;margin:0 0 8px}}
.note p{{margin:0 0 9px;font-size:.9rem}} .note p:last-child{{margin-bottom:0}}
.note .q{{color:var(--muted);font-size:.84rem}}
.assump{{width:100%;border-collapse:collapse;font-size:.82rem;margin-top:10px}}
.assump td{{padding:6px 0;border-bottom:1px solid var(--edge)}}
.assump td:first-child{{color:var(--muted)}}
.assump td:last-child{{text-align:right;font-weight:600}}
footer{{color:var(--faint);font-size:.76rem;margin-top:28px;display:flex;justify-content:space-between;
 flex-wrap:wrap;gap:8px}}
.na{{color:var(--faint);font-size:.78rem;border:1px dashed var(--edge);border-radius:999px;
 padding:5px 13px;opacity:.75;cursor:help}}
.partial{{background:var(--card);border:1px solid var(--edge);border-left:3px solid var(--gold);
 border-radius:12px;padding:14px 18px;margin-top:20px;color:var(--muted);font-size:.86rem;
 line-height:1.55;box-shadow:var(--shadow)}}
.partial b{{color:var(--ink)}}
.tag{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--edge);border-radius:999px;
 padding:3px 10px;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}}
#tt{{position:fixed;pointer-events:none;background:var(--hero);color:#eef3ef;border-radius:8px;padding:7px 10px;
 font-size:.8rem;opacity:0;transition:opacity .12s;z-index:9;white-space:nowrap}}
#tt .t2{{color:#9db3a8;font-size:.74rem}}
@media (max-width:780px){{.grid2{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Downsize / Sale-to-Rent Model</h1>
        <div class="meta">Hypothetical — all figures are estimates, editable on the "Downsize Model" sheet tab</div>
      </div>
      <span class="tag">● Demo data</span>
    </div>
    <nav class="topnav">
      {nav}
    </nav>
  </header>
  <div class="rule"></div>

  <section class="trend">
    <h2 class="serif">If we sold and rented instead</h2>
    <p class="period">Not financial advice — a what-if model to react to, not a plan. Proceeds-only:
      this treats the net sale proceeds as their own invested pot, not our whole portfolio. Every
      number below traces back to a labeled cell on the "Downsize Model" sheet tab or is pulled
      live from the transaction feed — nothing here is hardcoded in the script.</p>
    <p class="lead">Selling today would net an estimated <b>{usd(net_proceeds)}</b> after our mortgage,
      selling costs, and capital gains tax{mortgage_note}. {cmp_note}</p>

    <div class="grid2">
      <div class="mini"><h3>Net proceeds waterfall</h3>{waterfall_html}</div>
      <div class="mini"><h3>Gain &amp; tax</h3>
        <div class="row"><span>Cost basis (purchase + improvements)</span><span class="v num">{usd(f['basis'])}</span></div>
        <div class="row"><span>Realized gain</span><span class="v num">{usd(f['realized_gain'])}</span></div>
        <div class="row"><span>§121 exclusion (MFJ)</span><span class="v num">−{usd(a['Section 121 exclusion (MFJ)'])}</span></div>
        <div class="row tot"><span>Taxable gain</span><span class="v num">{usd(f['taxable_gain'])}</span></div>
        <div class="row"><span>Tax owed (fed {a['Federal LTCG rate']*100:.1f}% + NIIT {a['NIIT surtax']*100:.1f}% + CA {a['CA capital gains rate']*100:.1f}%)</span>
          <span class="v num">{usd(f['tax'])}</span></div>
      </div>
    </div>
  </section>

  <section class="trend">
    <h2 class="serif">How much rent could the proceeds sustain?</h2>
    <p class="period">Monte Carlo, {int(a['Monte Carlo trials']):,} trials, real terms (already
      inflation-adjusted — expected return {a['Expected real return (after-tax)']*100:.0f}%/yr,
      volatility {a['Return volatility (annual, real)']*100:.0f}%/yr). {inflation_note} Ruin is
      permanent — once a path hits zero it stays there. Each column is the largest monthly
      withdrawal that still succeeds (never runs out) in at least that fraction of the 1,000
      simulated paths. 50 years stands in for "indefinitely." Figures below are shown per month;
      the model itself simulates in annual terms.</p>
    <table class="htable">
      <thead><tr><th>Horizon</th><th>75% confidence /mo</th><th>90% confidence /mo</th><th>95% confidence /mo</th></tr></thead>
      <tbody>{headline_rows}</tbody>
    </table>
    <div id="curvechart" style="margin-top:18px"></div>
    <div class="legend" id="curvelegend"></div>
  </section>

  <section class="trend">
    <h2 class="serif">The tax benefit of owning (lost if you sell)</h2>
    <p class="period"><b>Not a CPA figure — for CPA review, not a substitute for one.</b> Mortgage
      interest and property tax are deductible; renting has no comparable deduction. This
      estimates that benefit and nets it out of the "cost of owning" comparison above, using the
      illustrative example rates and other-deductions baseline on the "Downsize Model" sheet tab,
      applied live to whatever mortgage interest and property tax the current 12-month window
      shows. Two deliberate simplifications, both explained where they apply: the federal side
      accounts for the standard deduction (see below — it matters, roughly halves the naive
      number); the California side does not, because it doesn't change the answer enough to be
      worth the complexity for our household specifically.</p>
    <div class="grid2">
      <div class="mini"><h3>Federal</h3>
        <div class="row"><span>Mortgage interest (current 12mo)</span><span class="v num">{usd(f['mortgage_interest'])}</span></div>
        <div class="row"><span>Property tax (current 12mo)</span><span class="v num">{usd(f['property_tax_paid'])}</span></div>
        <div class="row"><span>+ Other itemized (charity + other SALT)</span><span class="v num">{usd(f['other_itemized_excl_home'])}</span></div>
        <div class="row tot"><span>= Itemized, with the home</span><span class="v num">{usd(f['federal_itemized_with_home'])}</span></div>
        <div class="row"><span>vs. standard deduction / itemized without home</span><span class="v num">{usd(f['federal_baseline_deduction'])}</span></div>
        <div class="row tot"><span>Incremental deduction from owning</span><span class="v num">{usd(f['federal_incremental_deduction'])}</span></div>
        <div class="row"><span>× {a['Federal marginal ordinary rate']*100:.0f}% marginal rate</span>
          <span class="v num">{usd(f['federal_ownership_tax_benefit'])}/yr</span></div>
      </div>
      <div class="mini"><h3>California</h3>
        <div class="row"><span>Mortgage interest + property tax</span>
          <span class="v num">{usd(f['mortgage_interest'] + f['property_tax_paid'])}</span></div>
        <div class="row"><span>No SALT cap, no standard-deduction crowd-out modeled</span><span class="v num">—</span></div>
        <div class="row"><span>× {a['CA marginal ordinary rate']*100:.1f}% marginal rate</span>
          <span class="v num">{usd(f['ca_ownership_tax_benefit'])}/yr</span></div>
        <div class="row tot" style="margin-top:28px"><span>Combined tax benefit of owning</span>
          <span class="v num">{usd(ownership_tax_benefit)}/yr</span></div>
      </div>
    </div>
    <div class="lsub" style="margin-top:14px">Raw cash cost of owning: <b>{usd(housing_cost)}/yr</b>
      ({usd(housing_cost_mo)}/mo). Net of the tax benefit above: <b>{usd(effective_housing_cost)}/yr</b>
      ({usd(effective_housing_cost_mo)}/mo) — this is the figure compared against sustainable rent
      at the top of the page. Renting is assumed to get no itemized deduction of its own.</div>
  </section>

  <section class="note">
    <h2 class="serif">Assumptions (edit these on the "Downsize Model" sheet tab)</h2>
    <table class="assump">
      <tr><td>Current market value</td><td class="num">{usd(a['Current market value'])}</td></tr>
      <tr><td>Original purchase price</td><td class="num">{usd(a['Original purchase price'])}</td></tr>
      <tr><td>Capital improvements</td><td class="num">{usd(a['Capital improvements (basis add)'])}</td></tr>
      <tr><td>Remaining mortgage balance</td><td class="num">{usd(a['Remaining mortgage balance'])}</td></tr>
      <tr><td>Transfer tax + closing costs rate</td><td class="num">{(a['SD documentary transfer tax rate']+a['Agent + closing costs rate'])*100:.2f}%</td></tr>
      <tr><td>Combined cap-gains rate on taxable gain</td><td class="num">{(a['Federal LTCG rate']+a['NIIT surtax']+a['CA capital gains rate'])*100:.1f}%</td></tr>
      <tr><td>Federal / CA marginal ordinary rate (mortgage &amp; property tax)</td><td class="num">{a['Federal marginal ordinary rate']*100:.0f}% / {a['CA marginal ordinary rate']*100:.1f}%</td></tr>
      <tr><td>Expected real return / volatility</td><td class="num">{a['Expected real return (after-tax)']*100:.0f}% / {a['Return volatility (annual, real)']*100:.0f}%</td></tr>
      <tr><td>General inflation</td><td class="num">{inflation*100:.1f}%/yr</td></tr>
      <tr><td>Rent inflation</td><td class="num">{rent_inflation*100:.1f}%/yr</td></tr>
      <tr><td>→ Rent's real growth rate (derived)</td><td class="num">{real_rent_growth*100:+.1f}%/yr</td></tr>
      <tr><td>Cost of owning our house — raw cash / net of tax benefit</td><td class="num">{usd(housing_cost)} / {usd(effective_housing_cost)}</td></tr>
    </table>
    <p class="q" style="margin-top:12px">Not a CFP/CPA figure. Cap-gains rate is an illustrative
      example (15% federal bracket, not the 20% top rate) — check it against your own actual
      return, since a large enough gain could push into the 20% band. Selling now realizes and
      taxes the gain today; holding our house until death gives heirs a full step-up in basis
      instead, which is a real trade-off worth raising with the CFP separately from this model.</p>
  </section>

  {partial_note}
  <footer><span>Generated from the Tiller sheet · {f['generated'].strftime('%d %B %Y, %H:%M')}</span>
    <span>Synthetic data — not real, not financial advice</span></footer>
</div>
<div id="tt"><div class="t1"></div><div class="t2"></div></div>
<script>
const fmt=n=>"$"+Math.round(n).toLocaleString();
const tt=document.getElementById('tt');
function showTip(x,y,a,b){{tt.querySelector('.t1').textContent=a;tt.querySelector('.t2').textContent=b;
 tt.style.opacity=1;const r=tt.getBoundingClientRect();
 tt.style.left=Math.min(Math.max(x-r.width/2,8),innerWidth-r.width-8)+'px';tt.style.top=(y-r.height-14)+'px';}}
function hideTip(){{tt.style.opacity=0;}}
const cs=getComputedStyle(document.documentElement);
const col=n=>cs.getPropertyValue(n).trim();
const curves=[{curve_js}];
(function(){{
 const W=980,H=280,P={{l:52,r:16,t:14,b:34}};
 const colors={{20:col('--slate'),25:col('--green'),30:col('--gold'),50:col('--clay')}};
 const maxW=Math.max(...curves.flatMap(c=>c[1].map(p=>p[0])));
 const X=w=>P.l+(W-P.l-P.r)*w/maxW, Y=s=>P.t+(H-P.t-P.b)*(1-s);
 let g='';
 for(let i=0;i<=4;i++){{const s=i/4,y=Y(s);
   g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="${{col('--edge')}}" stroke-width="1"/>`+
      `<text x="${{P.l-8}}" y="${{y+4}}" fill="${{col('--faint')}}" font-size="10.5" text-anchor="end">${{(s*100).toFixed(0)}}%</text>`;}}
 for(let i=0;i<=4;i++){{const w=maxW*i/4,x=X(w);
   const anchor=i===0?'start':(i===4?'end':'middle');
   g+=`<text x="${{x}}" y="${{H-10}}" fill="${{col('--muted')}}" font-size="10.5" text-anchor="${{anchor}}">${{fmt(w)}}</text>`;}}
 let lines='';
 curves.forEach(([h,pts])=>{{
  const d=pts.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)).join(' ');
  lines+=`<path d="${{d}}" fill="none" stroke="${{colors[h]}}" stroke-width="2.25" stroke-linejoin="round" class="curveline" data-h="${{h}}"/>`;
 }});
 document.getElementById('curvechart').innerHTML=
  `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none" role="img" aria-label="Withdrawal vs success probability">${{g}}${{lines}}
   <text x="${{W/2}}" y="${{H-1}}" fill="${{col('--faint')}}" font-size="9.5" text-anchor="middle" opacity="0"> </text></svg>
   <div style="text-align:center;color:${{col('--faint')}};font-size:.72rem;margin-top:4px">monthly withdrawal (real $) →</div>`;
 document.getElementById('curvelegend').innerHTML=
  Object.entries(colors).map(([h,c])=>`<span><i style="background:${{c}}"></i>${{h}} years${{h==='50'?' (indefinite proxy)':''}}</span>`).join('');
}})();
</script>
</body>
</html>'''
    return T

if __name__ == '__main__':
    f = compute()
    open(OUT, 'w').write(build_html(f))
    print(f'wrote {OUT}')
    print(f"  home value {f['home_value']:,.0f} | net proceeds {f['net_proceeds']:,.0f} | "
          f"tax {f['tax']:,.0f} | housing cost/yr {f['housing_cost']:,.0f}")
    for h in HORIZONS:
        hd = f['headline'][h]
        print(f"  {h}yr: 75%={hd[0.75]:,.0f}  90%={hd[0.90]:,.0f}  95%={hd[0.95]:,.0f}")
