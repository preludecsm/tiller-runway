#!/usr/bin/env python3
"""
render_sustainability.py — regenerate reports/sustainability.html: is current household
spending sustainable against the whole portfolio, and how much could we actually spend?

This is the whole-portfolio extension of render_downsize.py's model, explicitly anticipated in
that script's docstring ("the natural thing to reuse if/when that bigger question gets built").
It answers a different question than the downsize model, not a bigger version of the same one:
  * Downsize model: invest an isolated pot (home-sale proceeds), nothing else going on, find the
    sustainable withdrawal.
  * This model: the WHOLE portfolio (current net worth, same figure as dashboard.html), PLUS a
    second, ongoing income stream (guaranteed income: Social Security + pensions, net of the same
    tax caveat used elsewhere) that offsets part of spending every year. The quantity being
    solved for is lifestyle spending (guaranteed income + portfolio draw combined) -- the same
    Layer 1 figure as dashboard.html.

  Capital-gains tax is deliberately EXCLUDED from the spend target (changed 2026-08-04, after an
  earlier version added it in and 2025's unusually large realized-gains tax bill exposed the
  flaw): doing so mixed a smoothed, hypothetical return assumption with an actual, lumpy
  historical tax bill that has no causal relationship to that assumption -- a bad simulated year
  would still have the full real tax bill subtracted even though a bad year realizes little/no
  gain and so would owe little/no real tax. "Expected real return (after-tax)" is already meant
  to absorb ongoing capital-gains tax drag inside that one number. A page note cites the same
  calendar-year tax figure as dashboard.html's tax section for reference, purely so the two pages
  don't silently disagree about what taxes were -- that figure is never added to spending here.

Deliberately its own self-contained simulate loop rather than importing render_downsize.py's
success_curve() -- that function only handles a single withdrawal stream with one escalation
rate; this one nets two streams (spending growing, income growing) against each other each year,
different enough semantics that forcing a shared abstraction wasn't worth it for two call sites.
Everything else about the mechanic is identical on purpose: real terms throughout, ruin is
permanent, horizons 20/25/30/50yr compared side by side, output is a withdrawal-vs-success
curve rather than one confidence level picked for you.

Assumptions live on their own "Portfolio Model" sheet tab -- deliberately independent of the
Downsize Model tab (separate pool, separate assumptions, matching this project's general
"duplicate rather than cross-reference" convention between config surfaces). Guaranteed income
and the SS/pension tax caveat are pulled live from the Transactions/Categories tabs, same
SSPENSION_CATS/PENSION_CATS/TAX_RATE_FED/TAX_RATE_CA constants as render_dashboard.py and
render_tiers.py (duplicated here on purpose, not imported).

  python3 render_sustainability.py
"""
import gspread, glob, os, re, html, sys
import numpy as np
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (PROJECT_ROOT, SHEET_ID, SHEET_NAME, HOUSEHOLD_NAME, SSPENSION_CATS, PENSION_CATS,
                     TAX_RATE_FED, TAX_RATE_CA)

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/sustainability.html'

HORIZONS = (20, 25, 30, 50)
WGRID_STEPS = 60
DRAWCAT = 'Investment Withdrawals'
TAXCATS = ('IRS', 'CA Tax')

# Same rate-based tax estimate as render_dashboard.py -- illustrative example rates, not tax
# advice. See config.py to set your own.

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
    # Optional page -- see the same note in render_downsize.py. Exit 3 means "custom tab absent",
    # which run_demo.sh treats as a skip rather than a failure.
    try:
        vals = book.worksheet('Portfolio Model').get_all_values()
    except gspread.WorksheetNotFound:
        print('No "Portfolio Model" tab on this sheet — skipping the Retirement Sustainability report.\n'
              '  This page is optional. To add it, see "Portfolio Model" in SETUP_GOOGLE_SHEETS.md.')
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

    mean_return = a['Expected real return (after-tax)']
    vol = a['Return volatility (annual, real)']
    trials = int(a['Monte Carlo trials'])
    primary_horizon = int(a['Primary horizon (years)'])
    inflation = a['General inflation']
    spend_growth_in = a['Lifestyle spending real growth']
    income_growth_in = a['Guaranteed income real growth']

    # current net worth -- identical Balance History logic to render_dashboard.py /
    # render_advisor_pdf.py (duplicated on purpose, not imported)
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
    acc = book.worksheet('Accounts').get_all_values()
    registry = set()
    for r in acc[1:]:
        if len(r) > 7 and r[7].strip(): registry.add(r[7].strip())
    if registry:
        obs = {a2: v for a2, v in obs.items() if a2 in registry}
    allobs = [d for v in obs.values() for d, _ in v]
    net_worth = 0.0
    if allobs:
        asof = max(allobs)
        for acct, series in obs.items():
            prior = [b for d, b in series if d <= asof]
            if not prior: continue
            b = prior[-1]
            net_worth += -b if cls.get(acct, '').startswith('liab') else b

    # current guaranteed income (net of the tax caveat) and current total spending -- same
    # 12-month window and category logic as render_dashboard.py
    cats = book.worksheet('Categories').get_all_values(); chdr = cats[0]
    ctype = {r[0].strip(): (r[2].strip() if len(r) > 2 else '') for r in cats[1:] if r and r[0].strip()}
    vals = book.worksheet('Transactions').get_all_values(); hdr = vals[0]
    ci = lambda n: hdr.index(n)
    D, CA, AM = ci('Date'), ci('Category'), ci('Amount')
    rows = []
    for r in vals[1:]:
        if len(r) <= AM: continue
        d = pdate(r[D]); amt = money(r[AM])
        if d is None or amt is None: continue
        rows.append((d, r[CA].strip(), amt))

    today = date.today()
    end = monthstart(today) - timedelta(days=1)
    start = addmonths(monthstart(end), -11)

    inc = draw = exp = tax = 0.0
    incsrc = defaultdict(float)
    for d, c, amt in rows:
        if not (start <= d <= end): continue
        t = ctype.get(c, '')
        if t == 'Income':
            if c == DRAWCAT: draw += amt
            else: inc += amt; incsrc[c] += amt
        elif t == 'Expense':
            exp += amt
            if c in TAXCATS: tax += amt
    life = exp - tax   # lifestyle spending, taxes held out (same convention as elsewhere)

    sspension = sum(incsrc.get(c, 0) for c in SSPENSION_CATS)
    pension = sum(incsrc.get(c, 0) for c in PENSION_CATS)
    tax_est = sspension * TAX_RATE_FED + pension * TAX_RATE_CA
    net_guaranteed_income = inc - tax_est

    # Capital-gains tax is deliberately NOT added to the spend target here (decided 2026-08-04,
    # after an unusually large realized-gains tax bill made the flaw obvious):
    # doing so mixed a smoothed, hypothetical return assumption with an actual, lumpy historical
    # tax bill that has no relationship to that assumption -- a bad simulated year would still
    # have the full real 2025 tax bill subtracted even though a bad year realizes little/no gain
    # and so would owe little/no real tax. "Expected real return (after-tax)" is already meant to
    # absorb ongoing capital-gains tax drag inside that one number, so spending is lifestyle only,
    # matching dashboard.html's Layer 1 exactly. (Considered instead tying tax to each simulated
    # year's own gain -- more realistic, but this portfolio is ~83% IRA where trading isn't a
    # taxable event, and 2025's bill was driven partly by a discretionary rebalance, not organic
    # appreciation, so a clean tax-as-a-function-of-return relationship would itself be a rough
    # approximation of an already-approximate thing. Not worth the complexity for this purpose.)
    current_total_spending = abs(life)
    current_portfolio_draw = max(0.0, current_total_spending - net_guaranteed_income)

    # calendar-year tax, purely for the reconciliation note -- same logic (and same `tyYYYY` Tags
    # convention) as render_dashboard.py's tax section, duplicated on purpose. Never added to the
    # spend target above; shown only so this page cites the same number the dashboard does rather
    # than silently disagreeing with it.
    Tg = hdr.index('Tags') if 'Tags' in hdr else None
    tax_by_year = defaultdict(lambda: defaultdict(float))
    for r in vals[1:]:
        if len(r) <= AM: continue
        d = pdate(r[D]); amt = money(r[AM])
        if d is None or amt is None: continue
        c = r[CA].strip()
        if c not in TAXCATS: continue
        year = d.year
        if Tg is not None and len(r) > Tg and r[Tg]:
            m = re.search(r'\bty(20\d\d)\b', r[Tg])
            if m: year = int(m.group(1))
        tax_by_year[year][c] += amt
    last_tax_year = today.year - 1
    last_tax_year_total = (abs(tax_by_year[last_tax_year].get('IRS', 0))
                            + abs(tax_by_year[last_tax_year].get('CA Tax', 0)))

    real_spend_growth = spend_growth_in
    real_income_growth = income_growth_in

    rng = np.random.default_rng(20260803)
    max_h = max(HORIZONS)
    returns = rng.normal(mean_return, vol, size=(trials, max_h))

    def success_curve(horizon):
        r = returns[:, :horizon]
        wmax = net_worth * mean_return * 2.5   # generous upper bound for the grid -- pegged to net
                                                # worth, not current_total_spending, so it stays a
                                                # valid search bound regardless of what "current"
                                                # spending is defined to include
        grid = np.linspace(0, wmax, WGRID_STEPS)
        spend_growth = (1 + real_spend_growth) ** np.arange(horizon)
        income_growth = (1 + real_income_growth) ** np.arange(horizon)
        curve = []
        for s in grid:
            bal = np.full(trials, net_worth)
            ruined = np.zeros(trials, dtype=bool)
            for yr in range(horizon):
                spend_yr = s * spend_growth[yr]
                income_yr = net_guaranteed_income * income_growth[yr]
                draw_yr = np.maximum(0.0, spend_yr - income_yr)
                bal = np.where(ruined, 0.0, bal * (1 + r[:, yr]) - draw_yr)
                ruined |= bal <= 0
            success = 1.0 - ruined.mean()
            curve.append((s, success))
        return curve

    curves = {h: success_curve(h) for h in HORIZONS}

    def spending_at(horizon, target):
        curve = curves[horizon]
        best = 0.0
        for s, success in curve:
            if success >= target: best = s
            else: break
        return best

    headline = {h: {p: spending_at(h, p) for p in (0.75, 0.90, 0.95)} for h in HORIZONS}

    def success_at_spending(horizon, spending):
        r = returns[:, :horizon]
        spend_growth = (1 + real_spend_growth) ** np.arange(horizon)
        income_growth = (1 + real_income_growth) ** np.arange(horizon)
        bal = np.full(trials, net_worth)
        ruined = np.zeros(trials, dtype=bool)
        for yr in range(horizon):
            spend_yr = spending * spend_growth[yr]
            income_yr = net_guaranteed_income * income_growth[yr]
            draw_yr = np.maximum(0.0, spend_yr - income_yr)
            bal = np.where(ruined, 0.0, bal * (1 + r[:, yr]) - draw_yr)
            ruined |= bal <= 0
        return 1.0 - ruined.mean()

    current_success = {h: success_at_spending(h, current_total_spending) for h in HORIZONS}

    return dict(titles=titles, a=a, net_worth=net_worth, net_guaranteed_income=net_guaranteed_income,
                current_total_spending=current_total_spending,
                current_portfolio_draw=current_portfolio_draw,
                last_tax_year=last_tax_year, last_tax_year_total=last_tax_year_total,
                inflation=inflation, real_spend_growth=real_spend_growth,
                real_income_growth=real_income_growth,
                mean_return=mean_return, vol=vol, trials=trials,
                primary_horizon=primary_horizon, curves=curves, headline=headline,
                current_success=current_success, generated=datetime.now())

def build_html(f):
    nav = nav_html('sustainability.html', f['titles']); partial_note = missing_note(f['titles'])
    a = f['a']
    net_worth = f['net_worth']
    net_guaranteed_income = f['net_guaranteed_income']
    current_total_spending = f['current_total_spending']
    last_tax_year = f['last_tax_year']
    last_tax_year_total = f['last_tax_year_total']
    headline = f['headline']
    ph = f['primary_horizon']

    ph_90 = headline[ph][0.90]
    ph_90_mo = ph_90 / 12
    current_mo = current_total_spending / 12
    delta_mo = abs(ph_90_mo - current_mo)
    equivalent = abs(ph_90 - current_total_spending) / current_total_spending <= 0.05 if current_total_spending else False
    cur_success_ph = f['current_success'][ph]
    if equivalent:
        cmp_note = (f'At 90% confidence over {ph} years, our portfolio could sustainably support about '
                    f'<b>{usd(ph_90_mo)}/mo</b> in total household spending — essentially equivalent to the '
                    f'{usd(current_mo)}/mo actually being spent today (within {usd(delta_mo)}/mo). '
                    f'Today\'s spending succeeds in <b>{cur_success_ph*100:.0f}%</b> of simulated paths over {ph} years.')
    else:
        more_less = "more" if ph_90 > current_total_spending else "less"
        verdict_word = "headroom" if ph_90 > current_total_spending else "a shortfall"
        cmp_note = (f'At 90% confidence over {ph} years, our portfolio could sustainably support about '
                    f'<b>{usd(ph_90_mo)}/mo</b> in total household spending — {more_less} than the '
                    f'{usd(current_mo)}/mo actually being spent today, suggesting {verdict_word} of roughly '
                    f'{usd(delta_mo)}/mo. Today\'s actual spending succeeds in <b>{cur_success_ph*100:.0f}%</b> '
                    f'of simulated paths over {ph} years.')

    inflation = f['inflation']; rsg = f['real_spend_growth']; rig = f['real_income_growth']
    def growth_phrase(rate, subject):
        if abs(rate) < 0.0005: return f'{subject} is assumed to track general inflation exactly (flat in real terms).'
        direction = 'above' if rate > 0 else 'below'
        return f'{subject} is assumed to run {abs(rate)*100:.1f}%/yr {direction} general inflation in real terms.'
    inflation_note = (f'General inflation is assumed at {inflation*100:.1f}%/yr. '
                       f'{growth_phrase(rsg, "Lifestyle spending")} '
                       f'{growth_phrase(rig, "Guaranteed income (Social Security + pensions)")}')

    headline_rows = ''.join(
        f'<tr><td>{h}{" (indefinite proxy)" if h == 50 else ""}</td>'
        f'<td class="num">{usd(headline[h][0.75]/12)}</td>'
        f'<td class="num">{usd(headline[h][0.90]/12)}</td>'
        f'<td class="num">{usd(headline[h][0.95]/12)}</td>'
        f'<td class="num">{f["current_success"][h]*100:.0f}%</td></tr>'
        for h in (20, 25, 30, 50))

    curve_js = ','.join(
        f'[{h},[{",".join(f"[{s/12:.0f},{succ:.4f}]" for s, succ in f["curves"][h])}]]' for h in (20, 25, 30, 50))

    prepared = f['generated'].strftime('%-d %B %Y')

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Retirement Sustainability — {html.escape(HOUSEHOLD_NAME)}</title>
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
.lsub{{font-size:.82rem;line-height:1.4;color:var(--muted);background:var(--paper);border:1px solid var(--edge);
 border-left:2px solid var(--green);border-radius:8px;padding:9px 13px;max-width:70ch}}
.lsub b{{color:var(--ink);font-weight:600}}
.trend .lead b{{color:var(--green)}}
.stat-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:14px}}
.stat{{background:var(--paper);border:1px solid var(--edge);border-radius:12px;padding:14px 16px}}
.stat .lbl{{font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600}}
.stat .val{{font-size:1.3rem;font-weight:600;letter-spacing:-.01em;margin-top:4px}}
.stat .sub{{font-size:.74rem;color:var(--muted);margin-top:2px}}
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
@media (max-width:780px){{.stat-row{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Retirement Sustainability</h1>
        <div class="meta">Whole portfolio — every number below is the actual net worth and spending, not a hypothetical</div>
      </div>
      <span class="tag">● Demo data</span>
    </div>
    <nav class="topnav">
      {nav}
    </nav>
  </header>
  <div class="rule"></div>

  <section class="trend">
    <h2 class="serif">Is current spending sustainable?</h2>
    <p class="period">Not financial advice — a model to react to, not a plan. Tests the WHOLE
      portfolio (current net worth) against lifestyle spending (guaranteed income + portfolio
      draw combined) — same Layer 1 figure as the Financial Summary page. Proceeds from a home
      sale are NOT included here — see the separate Downsize Model page for that, deliberately
      kept as its own isolated pot.</p>
    <p class="lead">{cmp_note}</p>
    <div class="stat-row">
      <div class="stat"><div class="lbl">Current net worth</div><div class="val num">${net_worth/1e6:.2f}M</div>
        <div class="sub">the whole simulated pot</div></div>
      <div class="stat"><div class="lbl">Guaranteed income (net)</div><div class="val num">{usd(net_guaranteed_income/12,0)}/mo</div>
        <div class="sub">SS + pensions, after the tax caveat</div></div>
      <div class="stat"><div class="lbl">Current total spending</div><div class="val num">{usd(current_total_spending/12,0)}/mo</div>
        <div class="sub">lifestyle only — see note on taxes below</div></div>
    </div>
    <div class="lsub" style="margin-top:14px">Capital-gains tax is deliberately excluded from
      spending here rather than added, unlike an earlier version of this page. Calendar-year
      {last_tax_year} taxes were <b>{usd(last_tax_year_total)}</b> (see the Financial Summary
      page) — a real, lumpy figure driven by that year's actual realized gains, which has no
      relationship to the smoothed {a["Expected real return (after-tax)"]*100:.0f}%/yr return assumption below. Adding it
      to every simulated year, including bad ones with little real gain and little real tax owed,
      overstated how much this model draws down our portfolio. "Expected real return (after-tax)"
      is assumed to already absorb ongoing capital-gains tax drag inside that one number instead.</div>
  </section>

  <section class="trend">
    <h2 class="serif">How much could we sustainably spend?</h2>
    <p class="period">Monte Carlo, {int(a['Monte Carlo trials']):,} trials, real terms (already
      inflation-adjusted — expected return {a['Expected real return (after-tax)']*100:.0f}%/yr,
      volatility {a['Return volatility (annual, real)']*100:.0f}%/yr). {inflation_note} Ruin is
      permanent — once a path hits zero it stays there. Each cell is our largest monthly total
      spending that still succeeds in at least that fraction of the 1,000 simulated paths; the
      last column is the success probability of TODAY'S actual spending level specifically.
      50 years stands in for "indefinitely." Figures are shown per month; the model simulates
      in annual terms.</p>
    <table class="htable">
      <thead><tr><th>Horizon</th><th>75% confidence /mo</th><th>90% confidence /mo</th><th>95% confidence /mo</th><th>Today's spending succeeds</th></tr></thead>
      <tbody>{headline_rows}</tbody>
    </table>
    <div id="curvechart" style="margin-top:18px"></div>
    <div class="legend" id="curvelegend"></div>
  </section>

  <section class="note">
    <h2 class="serif">Assumptions (edit these on the "Portfolio Model" sheet tab)</h2>
    <table class="assump">
      <tr><td>Current net worth</td><td class="num">${net_worth/1e6:.2f}M</td></tr>
      <tr><td>Guaranteed income (net of tax caveat)</td><td class="num">{usd(net_guaranteed_income)}/yr</td></tr>
      <tr><td>Current total spending (lifestyle only, tax excluded — see note above)</td><td class="num">{usd(current_total_spending)}/yr</td></tr>
      <tr><td>Expected real return / volatility</td><td class="num">{a['Expected real return (after-tax)']*100:.0f}% / {a['Return volatility (annual, real)']*100:.0f}%</td></tr>
      <tr><td>General inflation</td><td class="num">{inflation*100:.1f}%/yr</td></tr>
      <tr><td>Lifestyle spending real growth</td><td class="num">{rsg*100:+.1f}%/yr</td></tr>
      <tr><td>Guaranteed income real growth</td><td class="num">{rig*100:+.1f}%/yr</td></tr>
    </table>
    <p class="q" style="margin-top:12px">Not a CFP/CPA figure. Assumes the SAME 5%/12% real
      return/volatility as the Downsize Model page as a starting point — confirm against the
      portfolio's actual allocation when known, since the real portfolio may have a different risk
      posture than a hypothetical newly-invested pot. Guaranteed income is assumed fully COLA'd
      (flat in real terms); if either pension is known not to have COLA, set "Guaranteed income
      real growth" negative on the sheet to model its real value eroding over time. Proceeds-only
      downsizing and whole-portfolio sustainability are DIFFERENT questions — this page never
      assumes our house gets sold.</p>
  </section>

  {partial_note}
  <footer><span>Generated from the Tiller sheet · {f['generated'].strftime('%d %B %Y, %H:%M')}</span>
    <span>Synthetic data — not real, not financial advice</span></footer>
</div>
<script>
const cs = getComputedStyle(document.documentElement);
const col = n => cs.getPropertyValue(n).trim();
const fmt = n => "$" + Math.round(n).toLocaleString();
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
 const curX=X({current_total_spending/12:.0f});
 g+=`<line x1="${{curX}}" x2="${{curX}}" y1="${{P.t}}" y2="${{H-P.b}}" stroke="${{col('--faint')}}" stroke-width="1.5" stroke-dasharray="3 4"/>`+
    `<text x="${{curX}}" y="${{P.t-3}}" fill="${{col('--faint')}}" font-size="10" text-anchor="middle">today</text>`;
 let lines='';
 curves.forEach(([h,pts])=>{{
  const d=pts.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)).join(' ');
  lines+=`<path d="${{d}}" fill="none" stroke="${{colors[h]}}" stroke-width="2.25" stroke-linejoin="round"/>`;
 }});
 document.getElementById('curvechart').innerHTML=
  `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none" role="img" aria-label="Total spending vs success probability">${{g}}${{lines}}</svg>
   <div style="text-align:center;color:${{col('--faint')}};font-size:.72rem;margin-top:4px">monthly total spending (real $) →</div>`;
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
    print(f"  net worth {f['net_worth']:,.0f} | guaranteed income (net) {f['net_guaranteed_income']:,.0f} | "
          f"current spending {f['current_total_spending']:,.0f}")
    for h in HORIZONS:
        hd = f['headline'][h]; cs = f['current_success'][h]
        print(f"  {h}yr: 75%={hd[0.75]:,.0f}  90%={hd[0.90]:,.0f}  95%={hd[0.95]:,.0f}  "
              f"today's spending succeeds {cs*100:.0f}%")
