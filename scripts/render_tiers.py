#!/usr/bin/env python3
"""
render_tiers.py — regenerate reports/tiers.html: spending by cut-order tier, with a
funding-source waterfall (guaranteed income vs. portfolio draw).

Fixed/variable (what a financial advisor typically means, and what the Categories tab's own
Group/Tags columns used to encode — now deprecated, this project doesn't use them) answers a
corporate-budget question. This household's actual question is a retirement-drawdown one: if
guaranteed income or the portfolio took a hit, what changes, and in what order? So categories are
tiered by cut priority, not by nature:

  Tier 0 — Floor. Never cut. Housing carrying costs, utilities, core healthcare, groceries,
           basic transportation, unavoidable admin fees. This is the "floor number" guaranteed
           income is measured against elsewhere in this project.
  Tier 1 — First to trim. Recurring lifestyle that can be reduced without real hardship —
           dining out, subscriptions, personal care, routine home services.
  Tier 2 — Postponable. Can be delayed a year with no ongoing harm — home projects, travel,
           discretionary education.
  Tier 3 — Purely elective. Would only continue if things are going well — giving above a
           baseline.

Taxes and Investment Fees are excluded entirely, same as everywhere else in this project — they
are a cost of having income and a portfolio, not a lifestyle choice, and aren't part of
"lifestyle spending" in any of the other reports either.

Tier assignment lives on the Categories tab's Tier column (formalized 2026-08-01, was a hardcoded
dict here) — to change a category's tier, edit the sheet, no code change or redeploy needed. This
was a first pass and several categories were genuine judgment calls (Tesla Lease and Auto and Gas
as Tier 0 "some transportation is necessary" even though the specific choice is discretionary;
Home Maintenance as Tier 1 rather than Tier 0 despite deferred maintenance sometimes costing more
later) — the rationale for those is kept in a comment near EXCLUDED below, since a spreadsheet
cell can't hold it. A category present in the data with no Tier cell filled in defaults to Tier 1
and prints a warning, so a newly-added category can't silently vanish before someone tags it.

Funding-source waterfall: guaranteed income is allocated against tiers IN cut order (fills Tier 0
first, then 1, then 2, then 3); whatever a tier isn't covered by income is shown as draw-funded.
This is an allocation model, not a trace of actual dollars — money is fungible, income doesn't
literally "pay for" specific transactions — but it directly answers "if income only covered the
floor, how much of the rest is optional."

  python3 render_tiers.py
"""
import gspread, glob, os, re, html, json, sys
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (PROJECT_ROOT, SHEET_ID, SHEET_NAME, HOUSEHOLD_NAME, SSPENSION_CATS, PENSION_CATS,
                     TAX_RATE_FED, TAX_RATE_CA)

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/tiers.html'

TIER_NAMES = {
    0: 'Tier 0 — Floor (never cut)',
    1: 'Tier 1 — First to trim',
    2: 'Tier 2 — Postponable',
    3: 'Tier 3 — Purely elective',
}
TIER_DESC = {
    0: 'Housing, utilities, core healthcare, groceries, basic transportation, unavoidable admin. '
       'Matched against guaranteed income — this is the "floor number" elsewhere in this project.',
    1: 'Recurring lifestyle, reducible without real hardship. The first place a bad year shows up.',
    2: 'Can be delayed a year with no ongoing harm. Lumpy by nature, not just by amount.',
    3: 'Would only continue if things are going well.',
}
# Tier assignment now lives on the Categories tab's Tier column (formalized 2026-08-01, was a
# hardcoded dict here) -- edit the sheet to change a category's tier, no code change needed.
# Judgment calls made at the time, kept here since a spreadsheet cell can't hold rationale:
#   Dental: routine care is floor; the 2023-25 extraordinary course of work is already flagged
#     separately as a one-off elsewhere, not re-litigated here.
#   Auto and Gas / Tesla Lease: some transportation is necessary (Tier 0); the specific cost
#     level of the car itself is a separate, more discretionary choice this doesn't capture.
#   Home Maintenance: Tier 1, not 0 -- deferring can cost more later, but it's reducible/
#     postponable short-term, unlike a true floor item.
#   Cash: Tier 1 -- unallocated cash, now that Housekeeper is broken out as its own line.
EXCLUDED = ('IRS', 'CA Tax', 'Investment Fees')   # cost of income/portfolio, not a lifestyle choice

# Estimated tax cost of guaranteed income (SS + pension) -- see the matching constants and full
# rationale comment in render_dashboard.py. A rate, not a frozen dollar figure, applied fresh to
# this window's actual income -- illustrative example rates, not tax advice. See config.py.
DRAWCAT = 'Investment Withdrawals'
# funding-pool priority + colors, matching render_dashboard.py's existing routine/episodic split
# (var(--slate) for routine, var(--gold) for episodic there too — kept consistent, not reinvented)
POOLS = (('income', 'var(--green)', 'Guaranteed income'),
         ('routine', 'var(--slate)', 'Routine draw'),
         ('episodic', 'var(--gold)', 'Episodic draw'))

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

def compute():
    book = connect()
    cats = book.worksheet('Categories').get_all_values(); chdr = cats[0]
    ctype = {r[0].strip(): (r[2].strip() if len(r) > 2 else '') for r in cats[1:] if r and r[0].strip()}
    tcol = chdr.index('Tier') if 'Tier' in chdr else None
    TIERS = {}
    for r in cats[1:]:
        if not r or not r[0].strip() or tcol is None or len(r) <= tcol: continue
        m = re.match(r'Tier\s*(\d)', r[tcol].strip())
        if m: TIERS[r[0].strip()] = int(m.group(1))
    vals = book.worksheet('Transactions').get_all_values(); hdr = vals[0]
    ci = lambda n: hdr.index(n)
    D, DE, CA, AM, AC = ci('Date'), ci('Description'), ci('Category'), ci('Amount'), ci('Account')

    rows = []
    for r in vals[1:]:
        if len(r) <= AC: continue
        d = pdate(r[D]); a = money(r[AM])
        if d is None or a is None: continue
        rows.append((d, r[CA].strip(), a, r[DE].strip()))

    today = date.today()
    end = monthstart(today) - timedelta(days=1)
    w0_start = addmonths(monthstart(end), -11)
    wins = []
    for i in range(3):
        st = addmonths(w0_start, -12 * i)
        en = addmonths(st, 12) - timedelta(days=1)
        wins.append((st, en))
    wins.reverse()

    def agg(st, en):
        cat = defaultdict(float); inc = 0.0; draws = []; incsrc = defaultdict(float)
        for d, c, a, de in rows:
            if not (st <= d <= en): continue
            t = ctype.get(c, '')
            if t == 'Expense' and c not in EXCLUDED:
                cat[c] += a
            elif t == 'Income':
                if c == DRAWCAT: draws.append(a)
                else: inc += a; incsrc[c] += a
        cnt = Counter(draws)
        routine_amt = cnt.most_common(1)[0][0] if cnt else 0
        routine = sum(x for x in draws if x == routine_amt)
        episodic = sum(draws) - routine
        return dict(start=st, end=en, cat=cat, income=inc, routine=routine, episodic=episodic,
                    routine_amt=routine_amt, incsrc=incsrc)

    W = [agg(st, en) for st, en in wins]
    cur = W[-1]

    unassigned = sorted({c for w in W for c in w['cat']} - set(TIERS))
    if unassigned:
        print('WARNING: categories with real spend but no tier assignment (defaulting to Tier 1):',
              unassigned)
        for c in unassigned: TIERS[c] = 1

    def series(c):
        return [round(-W[i]['cat'].get(c, 0)) for i in range(3)]

    tier_rows = defaultdict(list)
    for c, tier in TIERS.items():
        s = series(c)
        if not any(s): continue
        tier_rows[tier].append(dict(name=c, vals=s))
    for tier in tier_rows:
        tier_rows[tier].sort(key=lambda x: -x['vals'][2])

    tier_totals = {t: [sum(r['vals'][i] for r in tier_rows.get(t, [])) for i in range(3)]
                   for t in (0, 1, 2, 3)}

    # funding-source waterfall, current window only: three pools (guaranteed income, routine
    # draw, episodic draw) fill categories in cut order (Tier 0 first, largest-current-year-first
    # within a tier, matching display order), each pool exhausted before the next is touched.
    # This is an allocation model, not a trace of literal dollars -- money is fungible -- but it
    # answers "what specifically is this spending funded by" in the same cut-priority terms as
    # the tiers themselves. Same routine/episodic split render_dashboard.py already computes.
    pools = {'income': cur['income'], 'routine': cur['routine'], 'episodic': cur['episodic']}
    cat_funding = {}
    for t in (0, 1, 2, 3):
        for r in tier_rows.get(t, []):
            remaining = r['vals'][2]  # current-year amount
            alloc = {}
            for key, _, _ in POOLS:
                take = max(0, min(remaining, pools[key]))
                alloc[key] = take
                pools[key] -= take
                remaining -= take
            cat_funding[r['name']] = alloc

    tier_funding = {t: {key: sum(cat_funding[r['name']][key] for r in tier_rows.get(t, []))
                         for key, _, _ in POOLS}
                     for t in (0, 1, 2, 3)}

    shown_cats = set(TIERS) & {c for w in W for c in w['cat']}
    txns = defaultdict(list)
    win_start, win_end = W[0]['start'], W[2]['end']
    for d, c, a, de in rows:
        if c not in shown_cats or not (win_start <= d <= win_end): continue
        txns[c].append((d, de, a))
    for c in txns: txns[c].sort(key=lambda t: t[0], reverse=True)

    return dict(W=W, cur=cur, tier_rows=tier_rows, tier_totals=tier_totals,
                tier_funding=tier_funding, cat_funding=cat_funding, txns=txns,
                generated=datetime.now())

def build_html(f):
    W = f['W']; cur = f['cur']
    def hdr(w): return f"{w['start'].strftime('%b ’%y')}–{w['end'].strftime('%b ’%y')}"
    headers = [hdr(w) for w in W]

    def fundbar(alloc):
        total = sum(alloc.values())
        if not total: return ''
        segs = ''.join(f'<span style="width:{alloc[key]/total*100:.2f}%;background:{color}"></span>'
                       for key, color, _ in POOLS if alloc[key] > 0)
        tip = ' · '.join(f'{label} {usd(alloc[key])}' for key, _, label in POOLS if alloc[key] > 0)
        return f'<span class="fundbar" title="{html.escape(tip, quote=True)}">{segs}</span>'

    def tier_section(t):
        rows_html = ''.join(
            f'<tr class="catrow" tabindex="0" data-cat="{html.escape(c["name"], quote=True)}">'
            f'<td>{html.escape(c["name"])}{fundbar(f["cat_funding"].get(c["name"], {}))}</td>'
            f'<td class="num">{usd(c["vals"][0])}</td><td class="num">{usd(c["vals"][1])}</td>'
            f'<td class="num now">{usd(c["vals"][2])}</td></tr>'
            f'<tr class="txrow" hidden><td colspan="4"><div class="txwrap"><div class="txlist"></div></div></td></tr>'
            for c in f['tier_rows'].get(t, []))
        tot = f['tier_totals'][t]
        return f'''
    <details class="bucket"{' open' if t == 0 else ''}>
      <summary><span class="bn">{html.escape(TIER_NAMES[t])}</span>
        <span class="bv num">{headers[0]}: {k(tot[0])} &nbsp;·&nbsp; {headers[1]}: {k(tot[1])}
          &nbsp;·&nbsp; {headers[2]}: <b>{k(tot[2])}</b></span></summary>
      <p class="tdesc">{html.escape(TIER_DESC[t])}</p>
      <table class="ctable"><thead><tr><th>Category (bar: current year's funding)</th><th>{headers[0]}</th><th>{headers[1]}</th><th>{headers[2]}</th></tr></thead>
      <tbody>{rows_html}</tbody></table>
    </details>'''

    tiers_html = ''.join(tier_section(t) for t in (0, 1, 2, 3))

    txns_json = json.dumps({
        cat: [[f'{d.month}/{d.day}/{d.year}', de, round(a, 2)] for d, de, a in lst]
        for cat, lst in f['txns'].items()
    }, separators=(',', ':'))

    tf = f['tier_funding']
    def wf_row(t):
        total = f['tier_totals'][t][2]
        if not total: return ''
        segs = ''.join(f'<span style="width:{tf[t][key]/total*100:.2f}%;background:{color}"></span>'
                       for key, color, _ in POOLS if tf[t][key] > 0)
        sub = ' &nbsp;·&nbsp; '.join(f'{usd(tf[t][key])} {label.lower()}' for key, _, label in POOLS if tf[t][key] > 0)
        return f'''
      <div class="wfrow">
        <div class="wfname">{html.escape(TIER_NAMES[t])}</div>
        <div class="wftrack">{segs}</div>
        <div class="wfnums"><span class="num">{usd(total)}</span>
          <span class="wfsub">{sub}</span></div>
      </div>'''
    waterfall_html = ''.join(wf_row(t) for t in (0, 1, 2, 3))
    TIER_COLORS = {0: 'var(--green)', 1: 'var(--slate)', 2: 'var(--gold)', 3: 'var(--clay)'}
    tier_total_all = sum(f['tier_totals'][t][2] for t in (0, 1, 2, 3)) or 1
    tierbar_html = ''.join(
        f'<div style="width:{f["tier_totals"][t][2]/tier_total_all*100:.2f}%;background:{TIER_COLORS[t]}">'
        f'{f["tier_totals"][t][2]/tier_total_all*100:.0f}%</div>'
        for t in (0, 1, 2, 3) if f['tier_totals'][t][2])
    tierbar_legend = ' &nbsp;&nbsp; '.join(
        f'<span class="lgd"><span class="lgdsw" style="background:{TIER_COLORS[t]}"></span>'
        f'{html.escape(TIER_NAMES[t])} — {usd(f["tier_totals"][t][2])}</span>'
        for t in (0, 1, 2, 3) if f['tier_totals'][t][2])
    floor_total = f['tier_totals'][0][2]
    floor_covered = tf[0]['income'] >= floor_total if floor_total else True
    spare_after_floor = max(0, cur['income'] - floor_total)
    legend_html = ' &nbsp;&nbsp; '.join(
        f'<span class="lgd"><span class="lgdsw" style="background:{color}"></span>{label}</span>'
        for _, color, label in POOLS)

    sspension = sum(cur['incsrc'].get(c, 0) for c in SSPENSION_CATS)
    pension = sum(cur['incsrc'].get(c, 0) for c in PENSION_CATS)
    tax_est = sspension * TAX_RATE_FED + pension * TAX_RATE_CA
    net_income = cur['income'] - tax_est
    net_spare = net_income - floor_total
    taxnote = (f'<p class="period">The {usd(cur["income"])} above is gross. An estimated '
               f'<b>~{usd(tax_est)}/yr</b> is owed in tax on Social Security and pension income '
               f'specifically (not withheld or split out in the feed) — net, only '
               f'<b>~{usd(net_income)}/yr</b> is really available, which '
               + (f'still covers the floor, with only ~{usd(net_spare)}/yr left for Tier 1 '
                  f'(vs. {usd(spare_after_floor)}/yr on the gross figure).'
                  if net_spare >= 0 else
                  f'<b>falls short of the floor by ~{usd(abs(net_spare))}/yr</b> — the gross '
                  f'figure alone overstates coverage.')
               + f' Rate ({TAX_RATE_FED*100:.1f}% federal / {TAX_RATE_CA*100:.1f}% CA) is an illustrative '
               + 'example marginal bracket, not a fixed feature of SS/pension — it moves '
               + 'with total income and should be re-derived from each new return. Not a CPA figure — see '
               + 'config.py.</p>')

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spending by Tier — {html.escape(HOUSEHOLD_NAME)}</title>
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
.bucket{{background:var(--card);border:1px solid var(--edge);border-radius:12px;margin-top:12px;
 box-shadow:var(--shadow);overflow:hidden}}
.bucket summary{{list-style:none;cursor:pointer;padding:14px 18px;display:flex;justify-content:space-between;
 align-items:baseline;gap:12px;flex-wrap:wrap}}
.bucket summary::-webkit-details-marker{{display:none}}
.bucket summary:before{{content:'\\25B8';display:inline-block;margin-right:8px;color:var(--faint);
 transition:transform .15s}}
.bucket[open] summary:before{{transform:rotate(90deg)}}
.bucket .bn{{font-weight:600;font-size:.95rem}}
.bucket .bv{{font-size:.82rem;color:var(--muted)}}
.tdesc{{margin:0;padding:0 18px 12px;color:var(--muted);font-size:.8rem;max-width:70ch}}
.ctable{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;border-top:1px solid var(--edge)}}
.ctable th{{font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
 font-weight:600;text-align:right;padding:8px 18px 6px}}
.ctable th:first-child{{text-align:left}}
.ctable td{{padding:8px 18px;border-top:1px solid var(--edge);text-align:right;font-size:.88rem}}
.ctable td:first-child{{text-align:left;color:var(--muted)}}
.ctable td.now{{font-weight:600;color:var(--ink)}}
.catrow{{cursor:pointer}}
.catrow:hover td{{background:var(--paper)}}
.catrow:focus-visible{{outline:2px solid var(--gold);outline-offset:-2px}}
.catrow td:first-child:before{{content:'\\25B8';display:inline-block;width:1em;margin-left:-1em;
 color:var(--faint);transition:transform .15s}}
.catrow.open td:first-child:before{{transform:rotate(90deg)}}
.txrow td{{padding:0;border-top:none}}
.txwrap{{max-height:340px;overflow-y:auto;background:var(--paper);border-top:1px solid var(--edge);
 border-bottom:1px solid var(--edge)}}
.txline{{display:grid;grid-template-columns:5.5em 1fr auto;gap:10px;padding:6px 18px;font-size:.82rem;
 border-top:1px solid var(--edge)}}
.txline:first-child{{border-top:none}}
.txd{{color:var(--faint)}}
.txn{{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.txa{{text-align:right}}
.txa.neg{{color:var(--ink)}}
.txa.pos{{color:var(--green)}}
.txempty{{padding:12px 18px;color:var(--faint);font-size:.82rem}}
.wfrow{{display:grid;grid-template-columns:11em 1fr 19em;gap:12px;align-items:center;padding:8px 0}}
.wfname{{font-size:.86rem}}
.wftrack{{height:10px;border-radius:5px;background:var(--grid);overflow:hidden;display:flex}}
.wftrack span{{display:block;height:100%}}
.wfnums{{text-align:right;font-size:.86rem}}
.wfsub{{display:block;color:var(--faint);font-size:.72rem;margin-top:1px}}
.lgd{{display:inline-flex;align-items:center;gap:5px;font-size:.76rem;color:var(--muted)}}
.lgdsw{{display:inline-block;width:9px;height:9px;border-radius:2px}}
.tierbar{{display:flex;height:34px;border-radius:6px;overflow:hidden}}
.tierbar div{{display:flex;align-items:center;justify-content:center;color:#fff;
 font-size:.74rem;font-weight:600;font-variant-numeric:tabular-nums}}
.fundbar{{display:inline-flex;width:38px;height:7px;border-radius:3px;overflow:hidden;
 vertical-align:middle;margin-left:9px;background:var(--grid)}}
.fundbar span{{display:block;height:100%}}
.good{{color:var(--green);font-weight:600}}
.flat{{color:var(--gold);font-weight:600}}
footer{{color:var(--faint);font-size:.76rem;margin-top:28px;display:flex;justify-content:space-between;
 flex-wrap:wrap;gap:8px}}
.tag{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--edge);border-radius:999px;
 padding:3px 10px;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Spending by Tier</h1>
        <div class="meta">12 months to {cur['end'].strftime('%B %Y')}</div>
      </div>
      <span class="tag">● Demo data</span>
    </div>
    <nav class="topnav">
      <a href="index.html">Overview</a>
      <a href="dashboard.html">Financial Summary</a>
      <a href="spending.html">Spending by Category</a>
      <a href="tiers.html" class="here">Spending by Tier</a>
      <a href="downsize.html">Downsize Model</a>
      <a href="sustainability.html">Retirement Sustainability</a>
    </nav>
  </header>
  <div class="rule"></div>

  <section class="trend">
    <h2 class="serif">Categorized by cut order, not by nature</h2>
    <p class="period">"Fixed vs. variable" answers a budgeting question; this answers a retirement one —
      if guaranteed income or our portfolio took a hit, what changes, and in what order? Categories are
      grouped by <b>Tier 0</b> (never cut) through <b>Tier 3</b> (purely elective). This is a first pass —
      several assignments are judgment calls; see the script's comments for the closest ones. Click a
      category to see the transactions behind it, same as the Spending by Category page. The small bar
      next to each category name is its own funding-source split for the current year — see the next
      section for what the colors mean. Taxes and Investment Fees are excluded — a cost of income and
      our portfolio, not a lifestyle choice.</p>
    <h2 class="serif" style="margin-top:22px;font-size:.9rem">The shape of spending</h2>
    <div class="tierbar">{tierbar_html}</div>
    <p class="period" style="margin-top:8px">{tierbar_legend}</p>
  </section>

  <section class="trend">
    <h2 class="serif">What's actually funding each tier</h2>
    <p class="period">Three pools, same priority order as the tiers themselves and the same
      routine/episodic split used on the Financial Summary: <b>guaranteed income</b>
      (<b>{usd(cur['income'])}</b>) fills Tier 0 first, then 1, then 2, then 3; whatever's left
      after that draws on the <b>routine draw</b> ({usd(cur['routine'])}/yr, at a standing
      {usd(cur['routine_amt'])}/mo), then the <b>episodic draw</b> ({usd(cur['episodic'])},
      mostly tax-funding). This is
      an allocation model, not a trace of literal dollars — money is fungible — but it answers
      "what specifically pays for this" in the same cut-priority terms as the tiers.</p>
    <p class="period">{"Guaranteed income covers the entire floor" if floor_covered else "Guaranteed income does NOT fully cover the floor"}{
      f", with {usd(spare_after_floor)}/yr left over for Tier 1." if floor_covered and spare_after_floor else "."}</p>
    {taxnote}
    <p class="period">{legend_html}</p>
    <div style="margin-top:6px">{waterfall_html}</div>
  </section>

  {tiers_html}

  <footer><span>Generated from the Tiller sheet · {f['generated'].strftime('%d %B %Y, %H:%M')}</span>
    <span>Synthetic data — not real, not financial advice</span></footer>
</div>
<script>
const TXNS={txns_json};
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmt=n=>'$'+Math.abs(n).toLocaleString(undefined,{{minimumFractionDigits:2,maximumFractionDigits:2}});
function txLine(t){{
 const neg=t[2]<0;
 return `<div class="txline"><span class="txd">${{t[0]}}</span><span class="txn">${{esc(t[1])}}</span>`+
        `<span class="txa ${{neg?'neg':'pos'}}">${{neg?'-':'+'}}${{fmt(t[2])}}</span></div>`;
}}
document.querySelectorAll('.catrow').forEach(row=>{{
 const panel=row.nextElementSibling, list=panel.querySelector('.txlist');
 const toggle=()=>{{
  if(panel.hidden && !panel.dataset.built){{
   const items=TXNS[row.dataset.cat]||[];
   list.innerHTML=items.length?items.map(txLine).join('')
    :'<div class="txempty">No transactions in this window.</div>';
   panel.dataset.built='1';
  }}
  panel.hidden=!panel.hidden; row.classList.toggle('open',!panel.hidden);
 }};
 row.addEventListener('click',toggle);
 row.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();toggle();}}}});
}});
</script>
</body>
</html>'''
    return T

if __name__ == '__main__':
    f = compute()
    open(OUT, 'w').write(build_html(f))
    print(f'wrote {OUT}')
    for t in (0, 1, 2, 3):
        tot = f['tier_totals'][t]
        tf = f['tier_funding'][t]
        print(f"  Tier {t}: {tot[0]:>8,.0f} -> {tot[1]:>8,.0f} -> {tot[2]:>8,.0f}  "
              f"(current: {tf['income']:,.0f} income + {tf['routine']:,.0f} routine + {tf['episodic']:,.0f} episodic)")
