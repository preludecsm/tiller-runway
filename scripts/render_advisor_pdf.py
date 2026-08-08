#!/usr/bin/env python3
"""
render_advisor_pdf.py — generate reports/advisor_summary.pdf, an 8-page printable summary meant
to be shared with the financial advisor.

This is deliberately a separate, minimal, AGGREGATE-ONLY generator — not a headless print of
dashboard.html/spending.html/tiers.html. Those pages embed a full per-category TXNS JSON blob for
click-to-expand, which must never leave your private network in a portable file. Every
figure here is a rollup (net worth, 3-year trend, tier composition, bucket totals); no individual
transaction ever appears.

Page 1 — overview: net worth, 3-year income/spend/draw trend.
Page 2 — the last 12 months in detail: household/taxes/portfolio, mirroring the three "layers" on
          dashboard.html's Financial Summary page exactly.
Page 3 — spending by tier (cut order) and what's funding it.
Page 4 — spending by top-level category (Bucket column), with data-derived notable movers.
Page 5 — downsize model: the sale (net proceeds waterfall, key assumptions).
Page 6 — downsize model: the tax benefit of owning (Federal/CA breakdown).
Page 7 — downsize model: the rent (sustainable-rent Monte Carlo table + curve).
Page 8 — whole-portfolio retirement sustainability (is current spending sustainable, and how
          much could the portfolio actually support).

A trailer "Notes" page (footnote markers + a paginated methodology appendix) existed from
2026-08-04 to 2026-08-05: headline numbers stayed on their page, longer methodology explanations
got a numbered marker and moved to the back. All of those notes were later pulled back onto the
page they annotate as plain <p class="caveat"> paragraphs (no marker) —
each page had few enough notes that a jump to the back was more friction than it was worth. Once
the last page's notes moved inline, the trailer page had nothing left to show, so it was removed
along with the footnote/pagination machinery. If a page ever needs a caveat too long to fit inline
again, add it as a <p class="caveat"> on that page rather than reintroducing the trailer.

Figures and phrasing mirror render_dashboard.py / render_tiers.py so the PDF never contradicts
the live site; the aggregation logic is duplicated here on purpose (each render_*.py script is
self-contained, no cross-imports, matching the existing pattern in this project) -- EXCEPT pages
5-7, which import compute() from render_downsize.py, and page 8, from render_sustainability.py,
rather than re-implementing their Monte Carlo engines. Those are complex enough (numpy, 1,000
trials) that duplicating them risked silent drift between the PDF and the live pages; a summary
pulling the exact same simulated distribution is more trustworthy than a hand-rolled
approximation of it. The full interactive curves stay on downsize.html / sustainability.html only
— this PDF shows headline tables.

  python3 render_advisor_pdf.py

Requires Google Chrome for the HTML -> PDF step (headless --print-to-pdf); no new Python deps.
"""
import gspread, glob, os, re, sys, html, subprocess, tempfile
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_downsize import compute as downsize_compute
from render_sustainability import compute as sustainability_compute
from config import (PROJECT_ROOT, SHEET_NAME, HOUSEHOLD_NAME, ADVISOR_NAME, CHROME,
                     SSPENSION_CATS, PENSION_CATS, TAX_RATE_FED, TAX_RATE_CA)

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/advisor_summary.pdf'

DRAWCAT = 'Investment Withdrawals'
TAXCATS = ('IRS', 'CA Tax')
EXCLUDED_TIER = ('IRS', 'CA Tax', 'Investment Fees')   # cost of income/portfolio, not a tier
FLAT_PCT = 8

# Same rate-based tax estimate as render_dashboard.py / render_tiers.py — illustrative example
# rates, not tax advice. See config.py to set your own.

TIER_NAMES = {
    0: 'Tier 0 — Floor (never cut)',
    1: 'Tier 1 — First to trim',
    2: 'Tier 2 — Postponable',
    3: 'Tier 3 — Purely elective',
}
TIER_DESC = {
    0: 'Housing, utilities, core healthcare, groceries, basic transportation, unavoidable admin.',
    1: 'Recurring lifestyle, reducible without real hardship.',
    2: 'Can be delayed a year with no ongoing harm.',
    3: 'Would only continue if things are going well.',
}
TIER_COLORS = {0: 'var(--tier0)', 1: 'var(--slate)', 2: 'var(--gold)', 3: 'var(--clay)'}
POOLS = (('income', 'var(--tier0)', 'Guaranteed income'),
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
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    return gspread.authorize(creds).open(SHEET_NAME)

def compute():
    book = connect()
    cats = book.worksheet('Categories').get_all_values(); chdr = cats[0]
    ctype = {r[0].strip(): (r[2].strip() if len(r) > 2 else '') for r in cats[1:] if r and r[0].strip()}
    bcol = chdr.index('Bucket') if 'Bucket' in chdr else None
    cbucket = {r[0].strip(): (r[bcol].strip() if bcol is not None and len(r) > bcol else '')
               for r in cats[1:] if r and r[0].strip()}
    tcol = chdr.index('Tier') if 'Tier' in chdr else None
    TIERS = {}
    for r in cats[1:]:
        if not r or not r[0].strip() or tcol is None or len(r) <= tcol: continue
        m = re.match(r'Tier\s*(\d)', r[tcol].strip())
        if m: TIERS[r[0].strip()] = int(m.group(1))

    vals = book.worksheet('Transactions').get_all_values(); hdr = vals[0]
    ci = lambda n: hdr.index(n)
    D, CA, AM, AC = ci('Date'), ci('Category'), ci('Amount'), ci('Account')
    rows = []
    for r in vals[1:]:
        if len(r) <= AC: continue
        d = pdate(r[D]); a = money(r[AM])
        if d is None or a is None: continue
        rows.append((d, r[CA].strip(), a))

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
        inc = draw = exp = tax = 0.0
        cat = defaultdict(float); incsrc = defaultdict(float); draws = []
        for d, c, a in rows:
            if not (st <= d <= en): continue
            t = ctype.get(c, '')
            if t == 'Income':
                if c == DRAWCAT: draw += a; draws.append(a)
                else: inc += a; incsrc[c] += a
            elif t == 'Expense':
                exp += a; cat[c] += a
                if c in TAXCATS: tax += a
        life = exp - tax
        cnt = Counter(draws)
        routine_amt = cnt.most_common(1)[0][0] if cnt else 0
        routine = sum(x for x in draws if x == routine_amt)
        return dict(start=st, end=en, income=inc, draw=draw, tax=tax, life=life,
                    gap=inc + life, cat=cat, incsrc=incsrc,
                    routine=routine, routine_amt=routine_amt, episodic=draw - routine)

    W = [agg(st, en) for st, en in wins]
    cur = W[-1]

    # bucket-level (top-level category) totals, current year only — same "blank Bucket = excluded"
    # rule as render_dashboard.py's own bucket_totals (income, transfers, taxes, investment fees
    # deliberately have no Bucket and drop out here, not by a separate filter).
    bucket_totals = defaultdict(float)
    for c, v in cur['cat'].items():
        b = cbucket.get(c, '')
        if b and v: bucket_totals[b] += v
    bucket_cur = {b: round(-v) for b, v in bucket_totals.items()}
    bucket_order = sorted(bucket_cur, key=lambda b: -bucket_cur[b])

    # same bucket totals for the two prior windows, purely to find 3-year movers for the notes —
    # not displayed as a full trend table on the page.
    bucket_series = {}
    for b in bucket_order:
        s = []
        for w in W:
            t = 0.0
            for c, v in w['cat'].items():
                if cbucket.get(c, '') == b: t += v
            s.append(round(-t))
        bucket_series[b] = s

    # tier rows/totals, mirroring render_tiers.py exactly (categories in EXCLUDED_TIER never enter
    # the tier system; anything else with real spend but no Tier tag defaults to Tier 1)
    unassigned = sorted({c for w in W for c in w['cat']} - set(TIERS) - set(EXCLUDED_TIER))
    for c in unassigned: TIERS[c] = 1

    def series(c): return [round(-W[i]['cat'].get(c, 0)) for i in range(3)]
    tier_rows = defaultdict(list)
    for c, tier in TIERS.items():
        if c in EXCLUDED_TIER: continue
        s = series(c)
        if not any(s): continue
        tier_rows[tier].append(dict(name=c, vals=s))
    for tier in tier_rows: tier_rows[tier].sort(key=lambda x: -x['vals'][2])
    tier_totals = {t: [sum(r['vals'][i] for r in tier_rows.get(t, [])) for i in range(3)]
                   for t in (0, 1, 2, 3)}

    # funding waterfall, current window: three pools consumed strictly in cut order (Tier 0 first,
    # then 1, 2, 3). Allocating at the tier level (rather than per-category then summing) gives an
    # identical result here, since every Tier-0 category is exhausted before Tier-1 processing
    # starts either way — see render_tiers.py for the per-category version of this same model.
    pools = {'income': cur['income'], 'routine': cur['routine'], 'episodic': cur['episodic']}
    tier_funding = {}
    for t in (0, 1, 2, 3):
        remaining = tier_totals[t][2]
        tf = {}
        for key, _, _ in POOLS:
            take = max(0, min(remaining, pools[key]))
            tf[key] = take
            pools[key] -= take
            remaining -= take
        tier_funding[t] = tf

    # tax-on-guaranteed-income caveat — same rate-based estimate as the live site
    sspension = sum(cur['incsrc'].get(c, 0) for c in SSPENSION_CATS)
    pension = sum(cur['incsrc'].get(c, 0) for c in PENSION_CATS)
    tax_est = sspension * TAX_RATE_FED + pension * TAX_RATE_CA
    net_income = cur['income'] - tax_est

    # tax paid by CALENDAR year (Jan-Dec), not the rolling window used elsewhere — identical logic
    # to render_dashboard.py's "2 · Taxes" layer, duplicated here rather than imported (see module
    # docstring on why each render_*.py stays self-contained).
    Tg = hdr.index('Tags') if 'Tags' in hdr else None
    tax_by_year = defaultdict(lambda: defaultdict(float))
    tyyear_tag_count = 0
    for r in vals[1:]:
        if len(r) <= AC: continue
        d = pdate(r[D]); a = money(r[AM])
        if d is None or a is None: continue
        c = r[CA].strip()
        if c not in TAXCATS: continue
        year = d.year
        if Tg is not None and len(r) > Tg and r[Tg]:
            m = re.search(r'\bty(20\d\d)\b', r[Tg])
            if m: year = int(m.group(1)); tyyear_tag_count += 1
        tax_by_year[year][c] += a
    last_full_year = today.year - 1
    tax_years = [last_full_year - 2, last_full_year - 1, last_full_year]

    # net worth from Balance History — identical logic to render_dashboard.py
    bh = book.worksheet('Balance History').get_all_values(); bhd = bh[0]
    bD, bA, bB = bhd.index('Date'), bhd.index('Account'), bhd.index('Balance')
    bC = bhd.index('Class') if 'Class' in bhd else None
    obs = defaultdict(list); cls = {}
    for r in bh[1:]:
        if len(r) <= bB: continue
        d = pdate(r[bD]); b = money(r[bB]); a = r[bA].strip()
        if d is None or b is None or not a: continue
        obs[a].append((d, b))
        if bC is not None and len(r) > bC: cls[a] = r[bC].strip().lower()
    for a in obs: obs[a].sort()
    acc = book.worksheet('Accounts').get_all_values()
    registry = set()
    for r in acc[1:]:
        if len(r) > 7 and r[7].strip(): registry.add(r[7].strip())
    if registry:
        obs = {a: v for a, v in obs.items() if a in registry}
    allobs = [d for v in obs.values() for d, _ in v]
    nw = []
    if allobs:
        first, last = monthstart(min(allobs)), monthstart(max(allobs))
        grid = []
        m = first
        while m <= last:
            grid.append(m); m = addmonths(m, 1)
        for m in grid:
            asof = addmonths(m, 1) - timedelta(days=1)
            tot = 0.0
            for a, ser in obs.items():
                prior = [b for d, b in ser if d <= asof]
                if not prior: continue
                b = prior[-1]
                tot += -b if cls.get(a, '').startswith('liab') else b
            nw.append((m, tot))
        nw = nw[-13:]

    # mortgage principal inside the current window — identical logic to render_dashboard.py's `mort`
    mort = {}
    loans = [a for a in cls if 'mortgage' in a.lower() or 'loan' in a.lower()]
    if loans:
        ln = loans[0]
        pts = obs.get(ln, [])
        inwin = [x for x in pts if cur['start'] <= x[0] <= cur['end'] + timedelta(days=15)]
        if len(inwin) >= 2:
            mort['principal'] = inwin[0][1] - inwin[-1][1]

    # notable movers for page 3 — derived, never hand-written, so it can't drift from the numbers
    movers = sorted(((bucket_series[b][2] - bucket_series[b][0], b) for b in bucket_order),
                     key=lambda x: -x[0])
    riser = movers[0] if movers and movers[0][0] > 2000 else None
    faller = movers[-1] if movers and movers[-1][0] < -2000 else None

    downsize = downsize_compute()
    sustainability = sustainability_compute()

    return dict(W=W, cur=cur, bucket_cur=bucket_cur, bucket_order=bucket_order,
                bucket_series=bucket_series, tier_rows=tier_rows, tier_totals=tier_totals,
                tier_funding=tier_funding, tax_est=tax_est, net_income=net_income,
                tax_by_year=tax_by_year, tax_years=tax_years, tyyear_tag_count=tyyear_tag_count,
                mort=mort, riser=riser, faller=faller, nw=nw, downsize=downsize,
                sustainability=sustainability, generated=datetime.now())

def build_html(f):
    total_pages = 8
    advisor_line = f'Prepared for {html.escape(ADVISOR_NAME)}.'

    W = f['W']; cur = f['cur']
    def hdr(w): return f"{w['start'].strftime('%b ’%y')}–{w['end'].strftime('%b ’%y')}"
    life = [abs(w['life']) for w in W]; inc = [w['income'] for w in W]
    gap = [abs(w['gap']) for w in W]; rate = [w['routine_amt'] for w in W]
    draw_tot = [w['draw'] for w in W]

    spend_word = ('has stayed flat' if abs(life[2]-life[0])/life[0] < FLAT_PCT/100
                  else ('has risen' if life[2] > life[0] else 'has fallen'))
    if rate[2] < rate[0]:
        draw_clause = f"the standing monthly draw was cut from {usd(rate[0])} to {usd(rate[2])}"
    elif rate[2] > rate[0]:
        draw_clause = f"the standing monthly draw rose from {usd(rate[0])} to {usd(rate[2])}"
    else:
        draw_clause = f"the standing monthly draw has held at {usd(rate[2])}"
    inc_clause = ('guaranteed income has roughly doubled' if inc[2] > inc[0] * 1.8
                  else f"guaranteed income has gone from {k(inc[0])} to {k(inc[2])}")
    lead1 = (f"Our household spending {spend_word}, {inc_clause}, and {draw_clause}. Together that has "
             f"{'closed' if gap[2] < gap[0] else 'widened'} the shortfall funded by our portfolio "
             f"by <b>{usd(abs(gap[2]-gap[0]))} a year</b>.")

    nw = f['nw']; latest = nw[-1][1] if nw else 0
    yr_ago = nw[0][1] if len(nw) > 1 else latest
    nwpct = (latest / yr_ago - 1) * 100 if yr_ago else 0
    lo = min(x[1] for x in nw) if nw else 0
    hi = max(x[1] for x in nw) if nw else 1
    pad = (hi - lo) * .3 or 1
    ymin, ymax = lo - pad, hi + pad
    nwjs = ','.join(f'["{d.strftime("%b ’%y")}",{v:.0f}]' for d, v in nw)
    trendjs = ','.join(
        f'["{hdr(W[i])}",{inc[i]:.0f},{draw_tot[i]:.0f},{life[i]:.0f}]' for i in range(3))

    # ---- page 2: the last 12 months in detail (mirrors dashboard.html's three layers exactly) ----
    d2_incrows_html = ''.join(
        f'<div class="row"><span>{html.escape(c)}</span><span class="v num">{usd(v)}</span></div>'
        for c, v in sorted(cur['incsrc'].items(), key=lambda x: -x[1]) if abs(v) > 50)
    d2_exprows_html = ''.join(
        f'<div class="row"><span>{html.escape(b)}</span><span class="v num">{usd(f["bucket_cur"][b])}</span></div>'
        for b in f['bucket_order'])

    d2_mortnote = ''
    if f['mort'].get('principal'):
        d2_mortnote = (f'Includes ~<b>{usd(f["mort"]["principal"])}/yr</b> of mortgage principal, '
                        f'which builds home equity rather than being consumed.')

    d2_tax_est = f['tax_est']; d2_net_income = f['net_income']
    d2_taxnote = (f'Guaranteed income above is gross — an estimated <b>~{usd(d2_tax_est)}/yr</b> is owed in '
                  f'tax on Social Security and pension income specifically, leaving roughly '
                  f'<b>~{usd(d2_net_income)}/yr</b> net. Rate-based estimate, not a CPA figure — full '
                  f'methodology on the next page.')

    d2_tax_by_year = f['tax_by_year']; d2_tax_years = f['tax_years']
    d2_last_tax_year = d2_tax_years[-1]
    d2_last_year_irs = abs(d2_tax_by_year[d2_last_tax_year].get('IRS', 0))
    d2_last_year_ca = abs(d2_tax_by_year[d2_last_tax_year].get('CA Tax', 0))
    d2_last_year_tax_total = d2_last_year_irs + d2_last_year_ca
    d2_tax_year_totals = [abs(d2_tax_by_year[y].get('IRS', 0)) + abs(d2_tax_by_year[y].get('CA Tax', 0))
                           for y in d2_tax_years]
    d2_tax_year_rows_html = ''.join(
        f'<div class="row{" tot" if y == d2_last_tax_year else ""}"><span>{y}</span>'
        f'<span class="v num">{usd(t)}</span></div>' for y, t in zip(d2_tax_years, d2_tax_year_totals))
    d2_tyyear_tag_count = f['tyyear_tag_count']
    if d2_tyyear_tag_count:
        d2_taxyear_note = (f'Grouped by payment date, except <b>{d2_tyyear_tag_count}</b> payment(s) tagged '
                            f'<code>ty&lt;year&gt;</code> on the Tags column to reassign them to the '
                            f'liability year they were actually for.')
    else:
        d2_taxyear_note = ('Grouped entirely by payment date — a January payment is often a Q4 estimate for '
                            'the prior year, and an April payment is ambiguous between a Q1 estimate for the '
                            'current year and a balance-due payment for the prior year\'s return.')

    d2_dr = [("Routine draw — the standing monthly rate", cur['routine']),
             ("Episodic draw — usually tax", cur['episodic'])]
    d2_drawrows_html = ''.join(
        f'<div class="row"><span>{html.escape(n)}</span><span class="v num">{usd(v)}</span></div>'
        for n, v in d2_dr if v)

    # ---- page 3: tier composition + funding ----
    tt = f['tier_totals']; tf = f['tier_funding']
    tier_total_all = sum(tt[t][2] for t in (0, 1, 2, 3)) or 1
    tierbar_html = ''.join(
        f'<div style="width:{tt[t][2]/tier_total_all*100:.2f}%;background:{TIER_COLORS[t]}">'
        f'{tt[t][2]/tier_total_all*100:.0f}%</div>'
        for t in (0, 1, 2, 3) if tt[t][2])
    tier_list_html = ''.join(
        f'<div class="tier-item"><span class="dot" style="background:{TIER_COLORS[t]};margin-top:5px"></span>'
        f'<span class="name">{html.escape(TIER_NAMES[t])}<span class="desc">{html.escape(TIER_DESC[t])}</span></span>'
        f'<span class="amt num">{usd(tt[t][2])}</span></div>'
        for t in (0, 1, 2, 3) if tt[t][2])

    floor_total = tt[0][2]
    floor_covered = tf[0]['income'] >= floor_total if floor_total else True
    spare_after_floor = max(0, cur['income'] - floor_total)
    funding_rows_html = ''
    for t in (0, 1, 2, 3):
        total = tt[t][2]
        if not total: continue
        parts = [(key, color, tf[t][key]) for key, color, _ in POOLS if tf[t][key] > 0]
        segs = ''.join(f'<div style="width:{v/total*100:.1f}%;background:{color}"></div>' for _, color, v in parts)
        nums = ' · '.join(usd(v) for _, _, v in parts)
        funding_rows_html += (f'<div class="funding-row"><div class="name">{html.escape(TIER_NAMES[t].split(" — ")[0])}</div>'
                              f'<div class="track">{segs}</div><div class="nums num">{nums}</div></div>')

    tax_est = f['tax_est']; net_income = f['net_income']
    net_spare = net_income - floor_total
    tax_note = (f'Rate-based estimate ({TAX_RATE_FED*100:.1f}% federal / {TAX_RATE_CA*100:.1f}% CA), '
                'not a CPA figure. This is our household\'s actual marginal tax bracket on the 2025 '
                'return, applied to Social Security and pension income specifically -- it moves with '
                'total income (especially realized capital gains) and should be re-derived from each '
                'new return, not trusted indefinitely.')
    taxnote2 = (f'Our {usd(cur["income"])} of guaranteed income above is gross. An estimated '
                f'<b>~{usd(tax_est)}/yr</b> is owed in tax on Social Security and pension income '
                f'specifically — net, roughly <b>~{usd(net_income)}/yr</b> is really available, which '
                + (f'still covers the floor, with ~{usd(max(0,net_spare))}/yr left for Tier 1.'
                   if net_spare >= 0 else
                   f'<b>falls short of the floor by ~{usd(abs(net_spare))}/yr</b>.')
                + f' {tax_note}')

    # ---- page 3: bucket-level spend ----
    bucket_cur = f['bucket_cur']; bucket_order = f['bucket_order']
    total_bucket_cur = sum(bucket_cur.values()) or 1
    top_bucket = bucket_order[0]
    top_pct = bucket_cur[top_bucket] / total_bucket_cur * 100
    max_bucket = max(bucket_cur.values()) if bucket_cur else 1
    bucket_bars_html = ''.join(
        f'<div class="lrow2"><span class="rl">{html.escape(b)}</span>'
        f'<span class="rv num">{usd(bucket_cur[b])}</span>'
        f'<span class="track2"><span class="fill2" style="width:{bucket_cur[b]/max_bucket*100:.1f}%"></span></span></div>'
        for b in bucket_order)

    verdict3 = (f'<b>{html.escape(top_bucket)}</b> is the largest tracked category this year at '
                f'<b>{usd(bucket_cur[top_bucket])}</b> — {top_pct:.0f}% of categorized spend.')

    notes = []
    riser, faller = f['riser'], f['faller']
    if riser:
        delta, b = riser
        s = f['bucket_series'][b]
        notes.append(f'<li><b>{html.escape(b)}</b> rose {usd(delta)} over three years '
                      f'({k(s[0])} → {k(s[2])}) — the biggest three-year increase of any category.</li>')
    if faller:
        delta, b = faller
        s = f['bucket_series'][b]
        notes.append(f'<li><b>{html.escape(b)}</b> fell {usd(abs(delta))} over three years '
                      f'({k(s[0])} → {k(s[2])}) — the biggest three-year decrease of any category.</li>')
    if not notes:
        notes.append('<li>No category moved by more than $2,000 over the three-year window.</li>')
    notes_html = ''.join(notes)

    # ---- page 4: downsize/sale-to-rent — the sale + tax benefit of owning ----
    ds = f['downsize']
    ds_waterfall = [
        ('Current market value', ds['home_value']),
        ('− Transfer tax + agent/closing costs', -ds['selling_costs']),
        ('− Mortgage payoff', -ds['mortgage_assumed']),
        ('− Capital gains tax (fed + NIIT + CA)', -ds['tax']),
        ('= Net proceeds to invest', ds['net_proceeds']),
    ]
    ds_waterfall_html = ''.join(
        f'<div class="row{" tot" if name.startswith("=") else ""}"><span>{html.escape(name)}</span>'
        f'<span class="v num">{"−" if v < 0 else ""}{usd(abs(v))}</span></div>' for name, v in ds_waterfall)

    ds_ltcg_note = (
        'Cap-gains rate is an illustrative example, not tax advice: taxable income here keeps all '
        'gains in the 15% federal bracket, not the 20% top rate. Worth checking against your own '
        'actual return each year, since a large enough gain can push into the 20% band. Selling now '
        'realizes and taxes the gain today; holding your house until death gives heirs a full '
        'step-up in basis instead, a separate trade-off worth raising with a CFP.')
    ds_fed_note = (
        'Federal: the naive "mortgage interest + property tax, times marginal rate" number '
        'OVERSTATES the benefit, because it ignores the standard deduction. If our remaining '
        'itemized deductions without the home (charity + other SALT) fall under the standard '
        'deduction, we\'d take the standard deduction instead of itemizing at all -- so the true '
        'marginal benefit is only the portion of itemizing WITH our home that exceeds the greater '
        'of the standard deduction or itemizing without our home, not the full deduction amount '
        '(see the row above). NOT A CPA FIGURE -- for CPA review, not a substitute for one.')
    ds_ca_note = (
        'California: standard-deduction crowd-out was deliberately NOT modeled here. CA\'s standard '
        'deduction is typically low enough relative to itemized deductions that itemizing on the '
        'CA return stays close to certain regardless of our home, so the naive calculation '
        '(deduction x CA marginal rate) matches the precise one closely enough not to be worth the '
        'extra complexity. CA also does not apply the federal SALT cap at all -- property tax is '
        'separately, fully deductible on the CA return.')

    ds_effective_cost_mo = ds['effective_housing_cost'] / 12

    # ---- page 6: downsize/sale-to-rent — how much rent the proceeds could sustain ----
    ds_curve_js = ','.join(
        f'[{h},[{",".join(f"[{w/12:.0f},{s:.4f}]" for w, s in ds["curves"][h])}]]' for h in (20, 25, 30, 50))
    ds_headline_rows = ''.join(
        f'<tr><td>{h}{" (indefinite proxy)" if h == 50 else ""}</td>'
        f'<td class="num">{usd(ds["headline"][h][0.75]/12)}</td>'
        f'<td class="num">{usd(ds["headline"][h][0.90]/12)}</td>'
        f'<td class="num">{usd(ds["headline"][h][0.95]/12)}</td></tr>'
        for h in (20, 25, 30, 50))
    ds_ph = ds['primary_horizon']
    ds_ph_90 = ds['headline'][ds_ph][0.90]
    ds_ph_90_mo = ds_ph_90 / 12
    ds_eff_cost_mo = ds['effective_housing_cost'] / 12
    ds_delta_mo = abs(ds_ph_90_mo - ds_eff_cost_mo)
    ds_rent_note = (
        'Real terms throughout (already inflation-adjusted): the return/volatility assumption and '
        'the withdrawal itself are both real, not nominal. Ruin is permanent -- once a simulated '
        'path hits zero it stays there, it cannot mathematically recover on a later good year. '
        'Proceeds-only model -- treats the net sale proceeds as their own isolated invested pot, '
        'not our whole portfolio (see the Retirement Sustainability page for that separate '
        'question). Interactive version, with the full curve across all four horizons, is on our '
        'household\'s downsize.html report.')
    ds_equivalent = abs(ds_ph_90 - ds['effective_housing_cost']) / ds['effective_housing_cost'] <= 0.05 if ds['effective_housing_cost'] else False
    if ds_equivalent:
        ds_cmp = (f'Invested, that could sustainably support about <b>{usd(ds_ph_90_mo)}/mo</b> in rent at '
                  f'90% confidence over {ds_ph} years — essentially equivalent to the '
                  f'{usd(ds_eff_cost_mo)}/mo it effectively costs to own our house today, net of '
                  f'our mortgage-interest/property-tax tax benefit from the previous page (within {usd(ds_delta_mo)}/mo) — '
                  f'renting at roughly what owning costs now would sit right at the edge of the sustainable range.')
    elif ds_ph_90 > ds['effective_housing_cost']:
        ds_cmp = (f'Invested, that could sustainably support about <b>{usd(ds_ph_90_mo)}/mo</b> in rent at '
                  f'90% confidence over {ds_ph} years — more than the {usd(ds_eff_cost_mo)}/mo it '
                  f'effectively costs to own our house today, net of our mortgage-interest/property-tax tax '
                  f'benefit from the previous page. That is roughly <b>{usd(ds_delta_mo)}/mo of headroom</b>: '
                  f'renting somewhere that costs as much as owning does today would still sit comfortably '
                  f'inside the 90%-confidence sustainable range, not right at its edge.')
    else:
        ds_cmp = (f'Invested, that could sustainably support about <b>{usd(ds_ph_90_mo)}/mo</b> in rent at '
                  f'90% confidence over {ds_ph} years — less than the {usd(ds_eff_cost_mo)}/mo it '
                  f'effectively costs to own our house today, net of our mortgage-interest/property-tax tax '
                  f'benefit from the previous page. That is roughly a <b>{usd(ds_delta_mo)}/mo shortfall</b>: '
                  f'renting somewhere that costs as much as owning does today would NOT be sustainable at 90% '
                  f'confidence — a comparable rental would need to cost less than owning does now.')

    # ---- page 6: whole-portfolio retirement sustainability ----
    su = f['sustainability']
    su_curve_js = ','.join(
        f'[{h},[{",".join(f"[{s/12:.0f},{succ:.4f}]" for s, succ in su["curves"][h])}]]' for h in (20, 25, 30, 50))
    su_headline_rows = ''.join(
        f'<tr><td>{h}{" (indefinite proxy)" if h == 50 else ""}</td>'
        f'<td class="num">{usd(su["headline"][h][0.75]/12)}</td>'
        f'<td class="num">{usd(su["headline"][h][0.90]/12)}</td>'
        f'<td class="num">{usd(su["headline"][h][0.95]/12)}</td>'
        f'<td class="num">{su["current_success"][h]*100:.0f}%</td></tr>'
        for h in (20, 25, 30, 50))
    su_note = (f'Spending shown is lifestyle only — calendar-year {su["last_tax_year"]} taxes '
               f'({usd(su["last_tax_year_total"])}, see page 2) are deliberately excluded. Assumes '
               f'the same {su["mean_return"]*100:.0f}%/{su["vol"]*100:.0f}% return/volatility as the '
               'downsize model, and guaranteed income fully COLA\'d (flat in real terms). Deliberately '
               'excludes any proceeds from selling our home.')

    prepared = f['generated'].strftime('%-d %B %Y')
    period = f"12 months to {cur['end'].strftime('%-d %B %Y')}"

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Household Financial Summary — Advisor Copy</title>
<style>
:root{{
  --card:#fff;--edge:#d5dbd4;--ink:#1a2420;--muted:#5b6a62;--faint:#8a988f;
  --gold:#8f6f34;--green:#2f6d54;--clay:#a9503b;--slate:#4a6572;
  --tier0:#6fb897;   /* lighter than --green on purpose -- --green and --slate print at nearly
                         identical luminance and were indistinguishable in the tier bar/legend */
}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:#fff;color:var(--ink);
 font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
.serif{{font-family:"Iowan Old Style",Georgia,"Times New Roman",serif}}
.num{{font-variant-numeric:tabular-nums}}
@page{{size:letter;margin:0}}
.page{{width:8.5in;height:11in;padding:.6in .85in;position:relative;font-size:.86rem;line-height:1.5;
 page-break-after:always;overflow:hidden}}
.page:last-child{{page-break-after:avoid}}
.letterhead{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid var(--ink);
 padding-bottom:14px;margin-bottom:20px}}
.letterhead .eyebrow{{font-size:.68rem;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);font-weight:600}}
.letterhead h1{{font-size:1.3rem;font-weight:600;margin:.15em 0 0;letter-spacing:-.01em}}
.letterhead .meta{{text-align:right;font-size:.76rem;color:var(--muted);line-height:1.5}}
.verdict{{font-size:1rem;line-height:1.45;max-width:58ch;margin:0 0 18px;color:var(--ink)}}
.verdict b{{color:var(--green)}}
.stat-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:18px}}
.stat{{border-left:2px solid var(--edge);padding-left:14px}}
.stat .lbl{{font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600}}
.stat .val{{font-size:1.4rem;font-weight:600;letter-spacing:-.01em;margin-top:3px}}
.stat .sub{{font-size:.74rem;color:var(--muted);margin-top:2px}}
h2.sec{{font-size:.9rem;font-weight:600;margin:0 0 3px;letter-spacing:-.005em}}
p.sub-sec{{color:var(--muted);font-size:.76rem;margin:0 0 8px;max-width:64ch}}
.section{{margin-bottom:14px}}
svg{{display:block;width:100%;height:auto}}
.trend-table{{width:100%;border-collapse:collapse;font-size:.78rem;margin-top:6px}}
.trend-table th{{text-align:right;font-size:.64rem;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);
 font-weight:600;padding:0 0 5px 10px;border-bottom:1px solid var(--edge)}}
.trend-table th:first-child{{text-align:left;padding-left:0}}
.trend-table td{{padding:5px 0 5px 10px;border-bottom:1px solid var(--edge);text-align:right}}
.trend-table td:first-child{{text-align:left;padding-left:0;color:var(--muted)}}
.trend-table td.now{{font-weight:600;color:var(--ink)}}
.trend-table tr:last-child td{{border-bottom:none}}
.legend{{display:flex;gap:18px;font-size:.72rem;color:var(--muted);margin-top:10px}}
.legend span{{display:inline-flex;align-items:center;gap:5px}}
.legend i{{width:9px;height:9px;border-radius:2px;display:inline-block}}
.tierbar{{display:flex;height:32px;border-radius:5px;overflow:hidden;margin-top:12px}}
.tierbar div{{display:flex;align-items:center;justify-content:center;color:#fff;font-size:.72rem;font-weight:600}}
.tier-list{{margin-top:14px;display:flex;flex-direction:column;gap:10px}}
.tier-item{{display:grid;grid-template-columns:1.6em 1fr auto;gap:10px;align-items:baseline}}
.tier-item .dot{{width:9px;height:9px;border-radius:2px;align-self:center}}
.tier-item .name{{font-size:.82rem}}
.tier-item .desc{{display:block;color:var(--faint);font-size:.72rem;margin-top:1px}}
.tier-item .amt{{font-weight:600;font-size:.86rem}}
.funding-row{{display:grid;grid-template-columns:6em 1fr 15em;align-items:center;gap:12px;margin-top:8px}}
.funding-row .name{{font-size:.78rem;color:var(--muted)}}
.funding-row .track{{height:9px;border-radius:4px;overflow:hidden;display:flex;background:var(--edge)}}
.funding-row .track div{{height:100%}}
.funding-row .nums{{text-align:right;font-size:.74rem;color:var(--muted)}}
.lrow2{{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:center;padding:5px 0}}
.lrow2 .rl{{font-size:.84rem}} .lrow2 .rv{{font-size:.84rem;color:var(--muted);text-align:right}}
.track2{{grid-column:1/-1;height:7px;border-radius:4px;background:var(--edge);overflow:hidden}}
.fill2{{display:block;height:100%;background:var(--green);border-radius:4px}}
.notes{{border-left:2px solid var(--gold);padding-left:14px;margin-top:22px}}
.notes h2.sec{{color:var(--ink)}}
.notes ul{{margin:0;padding-left:1.1em}}
.notes li{{margin:6px 0;font-size:.8rem;color:var(--muted)}}
.notes li b{{color:var(--ink);font-weight:600}}
.caveat{{font-size:.74rem;line-height:1.35;color:var(--muted);background:#f4f6f3;border:1px solid var(--edge);
 border-left:2px solid var(--green);border-radius:8px;padding:7px 12px;margin-top:8px;max-width:70ch}}
.caveat b{{color:var(--ink)}}
.mini{{background:#f8f9f7;border:1px solid var(--edge);border-radius:10px;padding:14px 16px}}
.mini h3{{font-size:.78rem;font-weight:600;margin:0 0 8px;color:var(--muted)}}
.mini .row{{display:flex;justify-content:space-between;gap:10px;font-size:.82rem;padding:4px 0}}
.mini .row span:last-child{{white-space:nowrap}}
.mini .row.tot{{border-top:1px solid var(--edge);margin-top:4px;padding-top:5px;font-weight:600}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:10px}}
.d2layer{{background:#fff;border:1px solid var(--edge);border-radius:9px;padding:9px 14px;margin-bottom:7px}}
.d2layer:last-child{{margin-bottom:0}}
.d2head{{display:flex;justify-content:space-between;align-items:baseline;gap:10px}}
.d2head h3{{font-size:.84rem;font-weight:600;margin:0;color:var(--ink)}}
.d2head .amt{{font-size:.96rem;font-weight:600;white-space:nowrap}}
.d2note{{color:var(--muted);font-size:.68rem;line-height:1.3;margin:3px 0 6px;max-width:none}}
.d2gap{{display:flex;justify-content:space-between;border-top:1px solid var(--edge);margin-top:5px;
 padding-top:4px;font-size:.74rem;font-weight:600}}
.d2layer .mini{{padding:7px 11px}}
.d2layer .mini h3{{font-size:.68rem;margin:0 0 3px}}
.d2layer .row{{font-size:.71rem;padding:1.5px 0}}
.d2layer .grid2{{gap:12px;margin-top:5px}}
.d2layer .caveat{{font-size:.68rem;padding:5px 10px;margin-top:5px;line-height:1.3}}
.rows2col{{column-count:2;column-gap:12px}}
.rows2col .row{{break-inside:avoid}}
.htable{{width:100%;border-collapse:collapse;font-size:.8rem;margin-top:6px}}
.htable th{{text-align:right;font-size:.62rem;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);
 font-weight:600;padding:0 0 4px 10px;border-bottom:1px solid var(--edge)}}
.htable th:first-child{{text-align:left;padding-left:0}}
.htable td{{padding:4px 0 4px 10px;border-bottom:1px solid var(--edge);text-align:right}}
.htable td:first-child{{text-align:left;padding-left:0;color:var(--muted)}}
.htable tr:last-child td{{border-bottom:none}}
footer.pgfoot{{position:absolute;bottom:.55in;left:.85in;right:.85in;display:flex;justify-content:space-between;
 font-size:.68rem;color:var(--faint);border-top:1px solid var(--edge);padding-top:9px}}
</style>
</head>
<body>

  <!-- PAGE 1 — OVERVIEW -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">{html.escape(HOUSEHOLD_NAME)}</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>{period}<br>{advisor_line}</div>
    </div>

    <p class="verdict">{lead1}</p>

    <div class="stat-row">
      <div class="stat"><div class="lbl">Net worth</div><div class="val num">${latest/1e6:.2f}M</div>
        <div class="sub">{nwpct:+.1f}% vs a year ago</div></div>
      <div class="stat"><div class="lbl">Guaranteed income</div><div class="val num">{k(cur['income'])}</div>
        <div class="sub">covers {cur['income']/abs(cur['life'])*100:.0f}% of spending</div></div>
      <div class="stat"><div class="lbl">Household gap</div><div class="val num">{k(abs(cur['gap']))}</div>
        <div class="sub">funded by our portfolio draw</div></div>
    </div>

    <div class="section">
      <h2 class="sec">Net worth, trailing 12 months</h2>
      <p class="sub-sec">Month-end, excludes home equity — our house isn't tracked as an asset, only its mortgage as a liability.</p>
      <div id="nwchart"></div>
    </div>

    <div class="section">
      <h2 class="sec">Three-year direction of travel</h2>
      <p class="sub-sec">Aug–Jul rolling years, so all three are complete 12-month periods rather than a partial current year.</p>
      <div id="trendchart" style="margin-top:6px"></div>
      <table class="trend-table">
        <thead><tr><th></th><th>{hdr(W[0])}</th><th>{hdr(W[1])}</th><th>{hdr(W[2])}</th></tr></thead>
        <tbody>
          <tr><td>Guaranteed income</td><td>{k(inc[0])}</td><td>{k(inc[1])}</td><td class="now">{k(inc[2])}</td></tr>
          <tr><td>Lifestyle spending</td><td>{k(life[0])}</td><td>{k(life[1])}</td><td class="now">{k(life[2])}</td></tr>
          <tr><td>Portfolio draw</td><td>{k(draw_tot[0])}</td><td>{k(draw_tot[1])}</td><td class="now">{k(draw_tot[2])}</td></tr>
          <tr><td>Household gap</td><td>{k(gap[0])}</td><td>{k(gap[1])}</td><td class="now">{k(gap[2])}</td></tr>
        </tbody>
      </table>
    </div>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 1 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 2 — THE LAST 12 MONTHS IN DETAIL -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">The last 12 months in detail</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>{period}<br>{advisor_line}</div>
    </div>

    <div class="d2layer">
      <div class="d2head"><h3>1 · Household — what our life costs</h3><span class="amt num">{usd(abs(cur['life']))}</span></div>
      <div class="d2note">Guaranteed income covers about {cur['income']/abs(cur['life'])*100:.0f}% of spending;
        the rest comes from the {usd(cur['routine_amt'])}/mo portfolio draw. Taxes are excluded here — see
        layer 2.{' ' + d2_mortnote if d2_mortnote else ''}</div>
      <div class="grid2" style="margin-top:0">
        <div class="mini"><h3>Our guaranteed income</h3>{d2_incrows_html}
          <div class="row tot"><span>Total</span><span class="v num">{usd(cur['income'])}</span></div></div>
        <div class="mini"><h3>What we spend</h3><div class="rows2col">{d2_exprows_html}</div></div>
      </div>
      <p class="caveat" style="margin-top:8px">{d2_taxnote}</p>
      <div class="d2gap"><span>Gap funded by our portfolio</span>
        <span class="num">−{usd(abs(cur['gap']))} · −{usd(abs(cur['gap'])/12)}/mo</span></div>
    </div>

    <div class="d2layer">
      <div class="d2head"><h3>2 · Taxes — a cost of income and portfolio returns</h3><span class="amt num">{usd(d2_last_year_tax_total)}</span></div>
      <div class="d2note">Not part of what we spend on ourselves — driven by interest, dividends and realized
        gains inside the portfolio. Shown by calendar year {d2_last_tax_year} (Jan–Dec), not the rolling window
        used elsewhere, since tax is actually owed on that basis.</div>
      <div class="grid2" style="margin-top:0">
        <div class="mini"><h3>Tax paid in for {d2_last_tax_year}</h3>
          <div class="row"><span>Federal (IRS)</span><span class="v num">{usd(d2_last_year_irs)}</span></div>
          <div class="row"><span>California (FTB)</span><span class="v num">{usd(d2_last_year_ca)}</span></div>
          <div class="row tot"><span>Total</span><span class="v num">{usd(d2_last_year_tax_total)}</span></div></div>
        <div class="mini"><h3>Why it swings (3yr)</h3>{d2_tax_year_rows_html}
          <div class="row tot"><span>3-year average</span><span class="v num">{usd(sum(d2_tax_year_totals)/3)}</span></div></div>
      </div>
      <p class="caveat" style="margin-top:8px">{d2_taxyear_note}</p>
    </div>

    <div class="d2layer">
      <div class="d2head"><h3>3 · Portfolio — the engine</h3><span class="amt num">{usd(cur['draw'])} drawn</span></div>
      <div class="d2note">The draw splits by purpose; investment returns over the period roughly replaced
        what was withdrawn.</div>
      <div class="mini">{d2_drawrows_html}
        <div class="row tot"><span>Total drawn</span><span class="v num">{usd(cur['draw'])}</span></div></div>
      <div class="d2gap"><span>Net worth, start → end</span>
        <span class="num">${yr_ago/1e6:.2f}M → ${latest/1e6:.2f}M</span></div>
    </div>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 2 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 3 — SPENDING BY TIER -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Spending by tier</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>{period}<br>{advisor_line}</div>
    </div>

    <p class="verdict">Categorized by <b>cut priority</b>, not by nature — if income or our portfolio
      took a hit, this is what changes, and in what order. {"Guaranteed income covers the entire floor" if floor_covered else "Guaranteed income does not fully cover the floor"}{
      f", with {usd(spare_after_floor)}/yr to spare." if floor_covered and spare_after_floor else "."}</p>

    <div class="section">
      <h2 class="sec">Spending by tier</h2>
      <p class="sub-sec">Tier 0 is never cut and is what guaranteed income is measured against; Tier 3 would only continue if things are going well.</p>
      <div class="tierbar">{tierbar_html}</div>
      <div class="tier-list">{tier_list_html}</div>
    </div>

    <div class="section">
      <h2 class="sec">What's funding each tier</h2>
      <p class="sub-sec">Guaranteed income fills tiers in cut order first; the routine draw (the standing
        monthly rate) covers the rest; the episodic draw is mostly tax-funding and contributes last, if at all.</p>
      <div class="legend">
        <span><i style="background:var(--tier0)"></i>Guaranteed income</span>
        <span><i style="background:var(--slate)"></i>Routine draw</span>
        <span><i style="background:var(--gold)"></i>Episodic draw</span>
      </div>
      <div>{funding_rows_html}</div>
      <div class="caveat">{taxnote2}</div>
    </div>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 3 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 4 — SPENDING BY CATEGORY -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Spending by category</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>{period}<br>{advisor_line}</div>
    </div>

    <p class="verdict">{verdict3}</p>

    <div class="section">
      <h2 class="sec">Top-level category spend, current year</h2>
      <p class="sub-sec">Each category groups several transaction-level categories — see our household's
        interactive report for the full breakdown. Taxes, investment fees, transfers and income are not
        categories and are excluded here.</p>
      <div>{bucket_bars_html}</div>
    </div>

    <div class="notes">
      <h2 class="sec">Notable this year</h2>
      <ul>{notes_html}</ul>
    </div>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 4 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 5 — DOWNSIZE MODEL: THE SALE -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Downsize model — the sale</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>Hypothetical, not current state<br>{advisor_line}</div>
    </div>

    <p class="verdict">Selling today would net an estimated <b>{usd(ds['net_proceeds'])}</b> after
      our mortgage, selling costs, and capital gains tax. Owning currently costs
      {usd(ds['housing_cost'])}/yr in cash — but a tax benefit worth an estimated
      <b>{usd(ds['ownership_tax_benefit'])}/yr</b> (mortgage interest and property tax
      deductions, lost if sold — full breakdown on the next page) brings the effective cost down
      to <b>{usd(ds['effective_housing_cost'])}/yr</b> ({usd(ds_effective_cost_mo)}/mo) — the
      figure compared against sustainable rent two pages from here.</p>

    <div class="grid2">
      <div class="mini"><h3>Net proceeds waterfall</h3>{ds_waterfall_html}</div>
      <div class="mini"><h3>Key assumptions</h3>
        <div class="row"><span>Current market value</span><span class="v num">{usd(ds['home_value'])}</span></div>
        <div class="row"><span>Combined cap-gains rate on taxable gain</span><span class="v num">{(ds['a']['Federal LTCG rate']+ds['a']['NIIT surtax']+ds['a']['CA capital gains rate'])*100:.1f}%</span></div>
        <div class="row"><span>Expected real return / volatility</span><span class="v num">{ds['mean_return']*100:.0f}% / {ds['vol']*100:.0f}%</span></div>
        <div class="row"><span>General / rent inflation</span><span class="v num">{ds['inflation']*100:.1f}% / {ds['rent_inflation']*100:.1f}%</span></div>
        <div class="row"><span>Cost of owning — raw / net of tax benefit</span><span class="v num">{usd(ds['housing_cost'])} / {usd(ds['effective_housing_cost'])}</span></div>
      </div>
    </div>

    <p class="caveat">{ds_ltcg_note}</p>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 5 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 6 — DOWNSIZE MODEL: THE TAX BENEFIT OF OWNING -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Downsize model — the tax benefit of owning</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>Hypothetical, not current state<br>{advisor_line}</div>
    </div>

    <p class="verdict">Mortgage interest and property tax are deductible; renting has no
      comparable deduction. Computed live from our actual mortgage interest and property tax over
      the current 12-month window, using the illustrative example rates and other-deductions
      baseline on the "Downsize Model" sheet tab — combined, worth an estimated
      <b>{usd(ds['ownership_tax_benefit'])}/yr</b>.</p>

    <div class="grid2">
      <div class="mini"><h3>Federal</h3>
        <div class="row"><span>Mortgage interest (current 12mo)</span><span class="v num">{usd(ds['mortgage_interest'])}</span></div>
        <div class="row"><span>Property tax (current 12mo)</span><span class="v num">{usd(ds['property_tax_paid'])}</span></div>
        <div class="row"><span>+ Other itemized (charity + other SALT)</span><span class="v num">{usd(ds['other_itemized_excl_home'])}</span></div>
        <div class="row tot"><span>= Itemized, with our home</span><span class="v num">{usd(ds['federal_itemized_with_home'])}</span></div>
        <div class="row"><span>vs. standard deduction / itemized without home</span><span class="v num">{usd(ds['federal_baseline_deduction'])}</span></div>
        <div class="row tot"><span>Incremental deduction from owning</span><span class="v num">{usd(ds['federal_incremental_deduction'])}</span></div>
        <div class="row"><span>× {ds['a']['Federal marginal ordinary rate']*100:.0f}% marginal rate</span><span class="v num">{usd(ds['federal_ownership_tax_benefit'])}/yr</span></div>
      </div>
      <div class="mini"><h3>California</h3>
        <div class="row"><span>Mortgage interest + property tax</span><span class="v num">{usd(ds['mortgage_interest'] + ds['property_tax_paid'])}</span></div>
        <div class="row"><span>× {ds['a']['CA marginal ordinary rate']*100:.1f}% marginal rate</span><span class="v num">{usd(ds['ca_ownership_tax_benefit'])}/yr</span></div>
        <div class="row tot" style="margin-top:120px"><span>Combined tax benefit of owning</span><span class="v num">{usd(ds['ownership_tax_benefit'])}/yr</span></div>
      </div>
    </div>

    <p class="caveat">{ds_fed_note}</p>
    <p class="caveat" style="margin-top:6px">{ds_ca_note}</p>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 6 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 7 — DOWNSIZE MODEL: THE RENT -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Downsize model — the rent</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>Hypothetical, not current state<br>{advisor_line}</div>
    </div>

    <p class="verdict" style="max-width:none">{ds_cmp}</p>

    <div class="section">
      <h2 class="sec">How much rent could the proceeds sustain?</h2>
      <p class="sub-sec" style="max-width:none">Monte Carlo, {int(ds['trials']):,} trials, real (inflation-adjusted) terms.
        Rent's real growth rate is derived from general vs. rent inflation ({ds['real_rent_growth']*100:+.1f}%/yr
        here); table shows the starting monthly amount, which stays flat only if that rate is 0%.
        Each cell is the largest such withdrawal that never runs out in at least that fraction
        of simulated paths.</p>
      <table class="htable">
        <thead><tr><th>Horizon</th><th>75% confidence /mo</th><th>90% confidence /mo</th><th>95% confidence /mo</th></tr></thead>
        <tbody>{ds_headline_rows}</tbody>
      </table>
      <div id="curvechart_ds" style="margin-top:14px"></div>
      <div class="legend" id="curvelegend_ds"></div>
    </div>

    <p class="caveat">{ds_rent_note}</p>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 7 of {total_pages}</span></footer>
  </div>

  <!-- PAGE 8 — RETIREMENT SUSTAINABILITY -->
  <div class="page">
    <div class="letterhead">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Retirement sustainability</h1>
      </div>
      <div class="meta">Prepared {prepared}<br>Whole portfolio, not a hypothetical<br>{advisor_line}</div>
    </div>

    <div class="grid2">
      <div class="mini"><h3>Whole-portfolio snapshot</h3>
        <div class="row"><span>Current net worth</span><span class="v num">${su['net_worth']/1e6:.2f}M</span></div>
        <div class="row"><span>Guaranteed income (net of tax caveat)</span><span class="v num">{usd(su['net_guaranteed_income'])}/yr</span></div>
        <div class="row"><span>Current total spending (lifestyle only)</span><span class="v num">{usd(su['current_total_spending'])}/yr</span></div>
      </div>
      <div class="mini"><h3>Key assumptions</h3>
        <div class="row"><span>Expected real return / volatility</span><span class="v num">{su['mean_return']*100:.0f}% / {su['vol']*100:.0f}%</span></div>
        <div class="row"><span>General inflation</span><span class="v num">{su['inflation']*100:.1f}%/yr</span></div>
        <div class="row"><span>Lifestyle / income real growth</span><span class="v num">{su['real_spend_growth']*100:+.1f}% / {su['real_income_growth']*100:+.1f}%</span></div>
        <div class="row"><span>Proceeds from a home sale included?</span><span class="v num">No</span></div>
      </div>
    </div>

    <div class="section" style="margin-top:14px">
      <h2 class="sec">How much could we sustainably spend?</h2>
      <p class="sub-sec" style="max-width:none">Monte Carlo, {int(su['trials']):,} trials, real (inflation-adjusted) terms.
        Each cell is our largest monthly lifestyle spending (guaranteed income + portfolio
        draw combined) that never runs out in at least that fraction of simulated paths. {su_note}</p>
      <table class="htable">
        <thead><tr><th>Horizon</th><th>75% confidence /mo</th><th>90% confidence /mo</th><th>95% confidence /mo</th><th>Today succeeds</th></tr></thead>
        <tbody>{su_headline_rows}</tbody>
      </table>
      <div id="curvechart_su" style="margin-top:14px"></div>
      <div class="legend" id="curvelegend_su"></div>
    </div>

    <footer class="pgfoot"><span>Personal and Confidential, generated from household transactions</span><span>Page 8 of {total_pages}</span></footer>
  </div>

<script>
const cs = getComputedStyle(document.documentElement);
const col = n => cs.getPropertyValue(n).trim();
const fmt = n => "$" + Math.round(n).toLocaleString();

(function(){{
  const nw=[{nwjs}];
  if(!nw.length) return;
  const W=680,H=150,P={{l:8,r:8,t:14,b:22}};
  const yMin={ymin:.0f}, yMax={ymax:.0f};
  const X=i=>P.l+(W-P.l-P.r)*i/(nw.length-1), Y=v=>P.t+(H-P.t-P.b)*(1-(v-yMin)/(yMax-yMin));
  let g=''; const step=(yMax-yMin)/3;
  for(let i=0;i<=3;i++){{const v=yMin+step*i,y=Y(v);
    g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="${{col('--edge')}}" stroke-width="1"/>`+
       `<text x="${{P.l}}" y="${{y-4}}" fill="${{col('--faint')}}" font-size="10">$${{(v/1e6).toFixed(2)}}M</text>`;}}
  const pts=nw.map((d,i)=>[X(i),Y(d[1])]);
  const line=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  const area=line+` L${{W-P.r}} ${{H-P.b}} L${{P.l}} ${{H-P.b}} Z`;
  const last=pts[pts.length-1];
  document.getElementById('nwchart').innerHTML=
    `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none">
     <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1">
     <stop offset="0" stop-color="${{col('--green')}}" stop-opacity=".22"/>
     <stop offset="1" stop-color="${{col('--green')}}" stop-opacity="0"/></linearGradient></defs>
     ${{g}}<path d="${{area}}" fill="url(#ga)"/>
     <path d="${{line}}" fill="none" stroke="${{col('--green')}}" stroke-width="2.25" stroke-linejoin="round"/>
     <circle cx="${{last[0]}}" cy="${{last[1]}}" r="4" fill="${{col('--gold')}}" stroke="#fff" stroke-width="1.5"/>
     <text x="${{last[0]-4}}" y="${{last[1]-10}}" fill="${{col('--ink')}}" font-size="11" font-weight="600" text-anchor="end">${{fmt(nw[nw.length-1][1])}}</text>
     <text x="${{P.l}}" y="${{H-4}}" fill="${{col('--faint')}}" font-size="10">${{nw[0][0]}}</text>
     <text x="${{W-P.r}}" y="${{H-4}}" fill="${{col('--faint')}}" font-size="10" text-anchor="end">${{nw[nw.length-1][0]}}</text></svg>`;
}})();

(function(){{
  const trend=[{trendjs}];
  const series=[['Guaranteed income',col('--green'),1],['Portfolio draw',col('--slate'),2],['Lifestyle spending',col('--clay'),3]];
  const W=680,H=170,P={{l:44,r:8,t:10,b:30}};
  const max=Math.max(...trend.flatMap(d=>[d[1],d[2],d[3]]));
  const Y=v=>P.t+(H-P.t-P.b)*(1-v/max);
  const groupW=(W-P.l-P.r)/trend.length, barW=groupW/(series.length+1.4);
  let bars='',axis='';const step=max/4;
  for(let i=0;i<=4;i++){{const v=step*i,y=Y(v);
    axis+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="${{col('--edge')}}" stroke-width="1"/>`+
          `<text x="${{P.l-6}}" y="${{y+3}}" fill="${{col('--faint')}}" font-size="9.5" text-anchor="end">$${{Math.round(v/1000)}}k</text>`;}}
  trend.forEach((d,gi)=>{{
    const gx=P.l+groupW*gi;
    series.forEach(([label,color,idx])=>{{
      const v=d[idx],y=Y(v),h=(H-P.b)-y,x=gx+barW*(idx-0.5);
      bars+=`<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{(barW*.82).toFixed(1)}}" height="${{h.toFixed(1)}}" fill="${{color}}" rx="1.5"/>`;}});
    bars+=`<text x="${{(gx+groupW/2).toFixed(1)}}" y="${{H-10}}" fill="${{col('--muted')}}" font-size="10.5" text-anchor="middle">${{d[0]}}</text>`;}});
  document.getElementById('trendchart').innerHTML=
    `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none">${{axis}}${{bars}}</svg>
     <div class="legend">${{series.map(s=>`<span><i style="background:${{s[1]}}"></i>${{s[0]}}</span>`).join('')}}</div>`;
}})();

function drawCurveChart(chartId, legendId, curves, xLabel, todayX){{
 const W=760,H=260,P={{l:48,r:14,t:12,b:30}};
 const colors={{20:col('--slate'),25:col('--green'),30:col('--gold'),50:col('--clay')}};
 const maxW=Math.max(...curves.flatMap(c=>c[1].map(p=>p[0])));
 const X=w=>P.l+(W-P.l-P.r)*w/maxW, Y=s=>P.t+(H-P.t-P.b)*(1-s);
 let g='';
 for(let i=0;i<=4;i++){{const s=i/4,y=Y(s);
   g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="${{col('--edge')}}" stroke-width="1"/>`+
      `<text x="${{P.l-7}}" y="${{y+3.5}}" fill="${{col('--faint')}}" font-size="10.5" text-anchor="end">${{(s*100).toFixed(0)}}%</text>`;}}
 for(let i=0;i<=4;i++){{const w=maxW*i/4,x=X(w);
   const anchor=i===0?'start':(i===4?'end':'middle');
   g+=`<text x="${{x}}" y="${{H-10}}" fill="${{col('--muted')}}" font-size="10.5" text-anchor="${{anchor}}">${{fmt(w)}}</text>`;}}
 if(todayX!=null){{
  const tx=X(todayX);
  g+=`<line x1="${{tx}}" x2="${{tx}}" y1="${{P.t}}" y2="${{H-P.b}}" stroke="${{col('--faint')}}" stroke-width="1.3" stroke-dasharray="3 3"/>`+
     `<text x="${{tx}}" y="${{P.t-3}}" fill="${{col('--faint')}}" font-size="10" text-anchor="middle">today</text>`;
 }}
 let lines='';
 curves.forEach(([h,pts])=>{{
  const d=pts.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)).join(' ');
  lines+=`<path d="${{d}}" fill="none" stroke="${{colors[h]}}" stroke-width="2.25" stroke-linejoin="round"/>`;
 }});
 document.getElementById(chartId).innerHTML=
  `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none">${{g}}${{lines}}</svg>
   <div style="text-align:center;color:${{col('--faint')}};font-size:.68rem;margin-top:2px">${{xLabel}}</div>`;
 document.getElementById(legendId).innerHTML=
  Object.entries(colors).map(([h,c])=>`<span><i style="background:${{c}}"></i>${{h}} years${{h==='50'?' (indefinite proxy)':''}}</span>`).join('');
}}
drawCurveChart('curvechart_ds','curvelegend_ds',[{ds_curve_js}],'monthly withdrawal (real $) →',null);
drawCurveChart('curvechart_su','curvelegend_su',[{su_curve_js}],'monthly total spending (real $) →',{su['current_total_spending']/12:.0f});
</script>
</body>
</html>'''
    return T

if __name__ == '__main__':
    f = compute()
    html_out = build_html(f)
    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False) as tf:
        tf.write(html_out)
        htmlpath = tf.name
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        result = subprocess.run([
            CHROME, '--headless', '--disable-gpu', '--no-pdf-header-footer',
            f'--print-to-pdf={OUT}', htmlpath,
        ], capture_output=True, text=True, timeout=60)
        print(result.stdout, result.stderr)
        if not os.path.exists(OUT):
            print('ERROR: PDF was not written'); sys.exit(1)
        size = os.path.getsize(OUT)
        print(f'wrote {OUT} ({size:,} bytes)')
    finally:
        os.unlink(htmlpath)

    cur = f['cur']
    print(f"  window {cur['start']} .. {cur['end']}")
    print(f"  net worth ${f['nw'][-1][1]/1e6:.2f}M | income {cur['income']:,.0f} | "
          f"life {abs(cur['life']):,.0f} | gap {cur['gap']:,.0f}")
    print(f"  buckets: {', '.join(f['bucket_order'])}")
