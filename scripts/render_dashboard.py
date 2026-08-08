#!/usr/bin/env python3
"""
render_dashboard.py — regenerate reports/dashboard.html from the live sheet.

Every figure on the page is computed here, so the dashboard cannot go stale. Windows roll:
the current one is the **last 12 complete months**, and the two before it are the preceding
12-month blocks. Column headers print the actual dates, so a rolling window is never mistaken
for a calendar year.

  python3 render_dashboard.py

Deliberate choices, kept consistent with how the numbers were reasoned about:
  * The portfolio draw is NOT income. It is your own money moving between accounts; counting it
    as income double-counts, because the matching fall in the portfolio is invisible at the
    transaction level. It gets its own layer.
  * Taxes are held out of lifestyle spending. Portfolio-gain tax round-trips (cash withdrawn to
    pay it, then paid) and would inflate both sides of the statement.
  * The draw is split into the routine standing rate and episodic withdrawals, because those
    answer different questions — the routine rate is a lifestyle decision, the episodic ones are
    usually tax.
  * The whole mortgage payment counts as spending (it is a cash statement) but the principal
    share is annotated, since that builds equity rather than being consumed.
"""
import gspread, glob, os, re, sys, html, statistics
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (PROJECT_ROOT, SHEET_NAME, HOUSEHOLD_NAME, SSPENSION_CATS, PENSION_CATS,
                     TAX_RATE_FED, TAX_RATE_CA)

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/dashboard.html'

TAXCATS = ('IRS', 'CA Tax')
FLAT_PCT = 8      # within this, a change reads as "flat" — used by BOTH the lead sentence and
                  # the verdict column, so they can never describe the same number differently
DRAWCAT = 'Investment Withdrawals'

# Estimated tax cost of guaranteed income (SS + pension). This is a RATE, not a frozen dollar
# figure, applied fresh to each window's actual income so it can't go stale the way a hardcoded
# number would — illustrative example rates, not tax advice. See config.py to set your own.

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
        scopes=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive'])
    return gspread.authorize(creds).open(SHEET_NAME)

def compute():
    book = connect()
    cats = book.worksheet('Categories').get_all_values(); chdr = cats[0]
    ctype = {r[0].strip(): (r[2].strip() if len(r) > 2 else '') for r in cats[1:] if r and r[0].strip()}
    bcol = chdr.index('Bucket') if 'Bucket' in chdr else None
    cbucket = {r[0].strip(): (r[bcol].strip() if bcol is not None and len(r) > bcol else '')
               for r in cats[1:] if r and r[0].strip()}
    vals = book.worksheet('Transactions').get_all_values(); hdr = vals[0]
    ci = lambda n: hdr.index(n)
    D, DE, CA, AM, AC = ci('Date'), ci('Description'), ci('Category'), ci('Amount'), ci('Account')

    rows = []
    for r in vals[1:]:
        if len(r) <= AC: continue
        d = pdate(r[D]); a = money(r[AM])
        if d is None or a is None: continue
        rows.append((d, r[CA].strip(), a, r[AC].strip(), r[DE]))

    # windows: last 12 COMPLETE months, and the two preceding blocks
    today = date.today()
    end = monthstart(today) - timedelta(days=1)          # last day of last complete month
    w0_start = addmonths(monthstart(end), -11)   # start of the most recent complete 12 months
    wins = []
    for i in range(3):
        st = addmonths(w0_start, -12*i)
        en = addmonths(st, 12) - timedelta(days=1)
        wins.append((st, en))
    wins.reverse()                                        # oldest first

    def agg(st, en):
        inc = draw = exp = tax = 0.0
        cat = defaultdict(float); incsrc = defaultdict(float); draws = []
        for d, c, a, acct, de in rows:
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
        return dict(start=st, end=en, income=inc, draw=draw, expense=exp, tax=tax, life=life,
                    gap=inc + life, cat=cat, incsrc=incsrc,
                    routine=routine, routine_amt=routine_amt, episodic=draw - routine)

    W = [agg(st, en) for st, en in wins]
    cur = W[-1]

    # Rationale for each trend row, derived rather than written by hand so it cannot go stale.
    why = {}
    # costs: if the middle period is the peak, name what pushed it there; else the biggest mover
    movers = sorted(((abs(W[1]['cat'].get(c, 0)) - abs(W[0]['cat'].get(c, 0)), c)
                     for c in set(W[0]['cat']) | set(W[1]['cat'])), reverse=True)
    if abs(W[1]['life']) > abs(W[0]['life']) and abs(W[1]['life']) > abs(W[2]['life']):
        top = [c for v, c in movers[:2] if v > 3000]
        if top: why['life'] = 'middle year’s peak was ' + ' and '.join(t.lower() for t in top)
        # category names are lowercased here because they read as common nouns in a sentence
    if 'life' not in why:
        big = sorted(((abs(W[2]['cat'].get(c, 0)) - abs(W[0]['cat'].get(c, 0)), c)
                      for c in set(W[0]['cat']) | set(W[2]['cat'])), reverse=True)
        if big and abs(big[0][0]) > 5000:
            why['life'] = f'mostly {big[0][1].lower()}'
    # income: name sources that began during the span
    started = [c for c in W[2]['incsrc']
               if W[2]['incsrc'][c] > 3000 and W[0]['incsrc'].get(c, 0) < 500]
    if started:
        why['income'] = ('both Social Security benefits started'
                         if len(started) > 1 and all('Social Security' in c for c in started)
                         else ' and '.join(started[:2]) + ' started')
    # draw: the date the standing rate last changed
    dr = sorted((d, a) for d, c, a, acct, de in rows if c == DRAWCAT)
    changed = None
    for i in range(len(dr) - 1, 0, -1):
        if dr[i][1] == cur['routine_amt'] and dr[i-1][1] != cur['routine_amt'] \
           and dr[i-1][1] in (w['routine_amt'] for w in W):
            changed = dr[i][0]; break
    if changed: why['draw'] = f'changed {changed.strftime("%B %Y")}'
    why['gap'] = None   # the figure speaks; filled below with the dollar change

    # lifestyle buckets for the current window, driven by the Categories tab's Bucket column
    # (Bucket/Tier live on the sheet, not hardcoded here, so editing them is a cell edit, not a
    # code change; a category with a blank Bucket is deliberately excluded — income, transfers,
    # taxes, investment fees)
    bucket_totals = defaultdict(float)
    for c, v in cur['cat'].items():
        b = cbucket.get(c, '')
        if b and v: bucket_totals[b] += v
    buckets = sorted(((name, round(-v)) for name, v in bucket_totals.items()), key=lambda x: -x[1])

    # Tax paid by CALENDAR year (Jan-Dec), not the rolling Aug-Jul window used everywhere else on
    # this page -- taxes are the one figure that's actually owed on a calendar-year basis (filing,
    # estimated-payment quarters), so slotting them into the fiscal window used for spending
    # analysis was misleading. Grouped by payment DATE by default, but a transaction tagged
    # `tyYYYY` in the Tags column (e.g. `ty2025`) is slotted into that year instead -- needed
    # because a January payment is very often a Q4 estimate for the PRIOR year, and an April
    # payment is ambiguous between a Q1 estimate for the current year and a balance-due payment
    # for the prior year's return. Tag any payment where the payment date doesn't match the
    # liability year; untagged payments fall back to their payment date.
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

    # net worth from Balance History: month-end, assets minus liabilities
    bh = book.worksheet('Balance History').get_all_values(); bhd = bh[0]
    bD, bA, bB = bhd.index('Date'), bhd.index('Account'), bhd.index('Balance')
    bC = bhd.index('Class') if 'Class' in bhd else None
    bS = bhd.index('Account Status') if 'Account Status' in bhd else None
    obs = defaultdict(list); cls = {}; status = {}
    for r in bh[1:]:
        if len(r) <= bB: continue
        d = pdate(r[bD]); b = money(r[bB]); a = r[bA].strip()
        if d is None or b is None or not a: continue
        obs[a].append((d, b))
        if bC is not None and len(r) > bC: cls[a] = r[bC].strip().lower()
        if bS is not None and len(r) > bS:
            prev = status.get(a)
            if prev is None or prev[0] <= d: status[a] = (d, r[bS].strip().upper())
    for a in obs: obs[a].sort()
    # Restrict to the accounts on the Accounts tab. That tab is Tiller's own registry and is the
    # only reliable filter: Balance History retains records for renamed and superseded accounts
    # (three "Traditional Gold Card" variants, an all-caps "AMERIPRISE ONE ACCT" beside the real
    # one), and their Account Status still reads ACTIVE years after they stopped reporting.
    # Including them overstates net worth; excluding everything stale drops genuinely dormant but
    # real holdings such as the Salaried Savings Plan. Summing this registry reproduces the
    # Balances tab exactly.
    acc = book.worksheet('Accounts').get_all_values()
    registry = set()
    for r in acc[1:]:
        if len(r) > 7 and r[7].strip(): registry.add(r[7].strip())
    if registry:
        obs = {a: v for a, v in obs.items() if a in registry}

    # Month-end net worth, carrying each account's last known balance forward. An account that
    # stops reporting must not silently drop out of the total — that understates net worth and
    # can invert the trend. Some months have no snapshots at all, so the series is built from a
    # month grid rather than from whatever happens to be present.
    allobs = [d for v in obs.values() for d, _ in v]
    if allobs:
        first, last = monthstart(min(allobs)), monthstart(max(allobs))
        grid = []
        m = first
        while m <= last:
            grid.append(m); m = addmonths(m, 1)
        nw = []
        for m in grid:
            asof = addmonths(m, 1) - timedelta(days=1)      # month end
            tot = 0.0
            for a, series in obs.items():
                prior = [b for d, b in series if d <= asof]
                if not prior: continue                       # account not open yet
                b = prior[-1]
                tot += -b if cls.get(a, '').startswith('liab') else b
            nw.append((m, tot))
        nw = nw[-13:]
    else:
        nw = []

    # mortgage principal inside the current window
    mort = {}
    loans = [a for a in cls if 'mortgage' in a.lower() or 'loan' in a.lower()]
    if loans:
        ln = loans[0]
        pts = obs.get(ln, [])
        inwin = [x for x in pts if cur['start'] <= x[0] <= cur['end'] + timedelta(days=15)]
        if len(inwin) >= 2:
            mort['principal'] = inwin[0][1] - inwin[-1][1]
            mort['paid'] = abs(cur['cat'].get('Mortgage', 0))
    return dict(W=W, cur=cur, buckets=buckets, nw=nw, mort=mort, why=why,
                tax_by_year=tax_by_year, tax_years=tax_years, tyyear_tag_count=tyyear_tag_count,
                generated=datetime.now())

def build_html(f):
    cur = f['cur']; W = f['W']
    nw = f['nw']; latest = nw[-1][1] if nw else 0
    yr_ago = nw[0][1] if len(nw) > 1 else latest
    nwpct = (latest / yr_ago - 1) * 100 if yr_ago else 0
    lo = min(x[1] for x in nw) if nw else 0
    hi = max(x[1] for x in nw) if nw else 1
    pad = (hi - lo) * .35 or 1
    ymin, ymax = lo - pad, hi + pad
    nwjs = ',' .join(f'["{d.strftime("%b ’%y")}",{v:.0f}]' for d, v in nw)

    def hdr(w): return f"{w['start'].strftime('%b ’%y')}–{w['end'].strftime('%b ’%y')}"
    life = [abs(w['life']) for w in W]; inc = [w['income'] for w in W]
    gap = [abs(w['gap']) for w in W]; rate = [w['routine_amt'] for w in W]
    draw_tot = [w['draw'] for w in W]
    trendjs = ','.join(
        f'["{hdr(W[i])}",{inc[i]:.0f},{draw_tot[i]:.0f},{life[i]:.0f}]' for i in range(3))

    def verdict(a, b, good_down=True, note=None):
        """Direction plus the reason for it. The reason is derived from the data, never written
        by hand, so it cannot drift out of step with the figures beside it."""
        ch = (b - a) / a * 100 if a else 0
        if abs(ch) < FLAT_PCT:
            lead = '<span class="flat">Flat</span>'
        else:
            down = ch < 0
            cls = 'good' if (down == good_down) else 'flat'
            word = ('Doubled' if (not down and b > a * 1.8)
                    else ('Down' if down else 'Up') + f' {abs(ch):.0f}%')
            lead = f'<span class="{cls}">{word}</span>'
        return lead + (f' — {note}' if note else '')

    spend_word = ('has stayed flat' if abs(life[2]-life[0])/life[0] < FLAT_PCT/100
                  else ('has risen' if life[2] > life[0] else 'has fallen'))
    if rate[2] < rate[0]:
        draw_clause = (f"we cut our standing monthly draw from {usd(rate[0])} to {usd(rate[2])}")
    elif rate[2] > rate[0]:
        draw_clause = (f"our standing monthly draw rose from {usd(rate[0])} to {usd(rate[2])}")
    else:
        draw_clause = f"our standing monthly draw has held at {usd(rate[2])}"
    inc_clause = ('our guaranteed income has roughly doubled' if inc[2] > inc[0]*1.8
                  else f"our guaranteed income has gone from {k(inc[0])} to {k(inc[2])}")
    lead = (f"Our spending {spend_word}, {inc_clause}, and {draw_clause}. Together that has "
            f"{'closed' if gap[2] < gap[0] else 'widened'} the shortfall by "
            f"<b>{usd(abs(gap[2]-gap[0]))} a year</b>.")

    bars = ','.join(f'["{html.escape(n)}",{v}]' for n, v in f['buckets'])
    incrows = ','.join(f'["{html.escape(c)}",{round(v)}]'
                       for c, v in sorted(cur['incsrc'].items(), key=lambda x: -x[1]) if abs(v) > 50)

    tax_by_year = f['tax_by_year']; tax_years = f['tax_years']
    last_tax_year = tax_years[-1]
    last_year_irs = abs(tax_by_year[last_tax_year].get('IRS', 0))
    last_year_ca = abs(tax_by_year[last_tax_year].get('CA Tax', 0))
    last_year_tax_total = last_year_irs + last_year_ca
    tax_year_totals = [abs(tax_by_year[y].get('IRS', 0)) + abs(tax_by_year[y].get('CA Tax', 0))
                        for y in tax_years]
    tax_year_rows = ''.join(
        f'<div class="row{" tot" if y == last_tax_year else ""}"><span>{y}</span>'
        f'<span class="v num">{usd(t)}</span></div>' for y, t in zip(tax_years, tax_year_totals))
    tyyear_tag_count = f['tyyear_tag_count']
    if tyyear_tag_count:
        taxyear_note = (f'<div class="lsub">Grouped by payment date, except <b>{tyyear_tag_count}</b> '
                         f'payment(s) tagged <code>ty&lt;year&gt;</code> on the Tags column to reassign them to '
                         f'the liability year they were actually for.</div>')
    else:
        taxyear_note = (f'<div class="lsub">Grouped entirely by payment date — a January payment is often a '
                         f'Q4 estimate for the <i>prior</i> year, and an April payment is ambiguous between a '
                         f'Q1 estimate for the current year and a balance-due payment for the prior year\'s '
                         f'return. Tag a payment <code>ty&lt;year&gt;</code> (e.g. <code>ty2025</code>) on the '
                         f'Tags column to reassign it to the year it was actually for — none tagged yet.</div>')
    mortnote = ''
    if f['mort'].get('principal'):
        pr = f['mort']['principal']
        mortnote = (f'<div class="lsub">Includes ~<b>{usd(pr)}/yr</b> of mortgage principal, which '
                    f'builds home equity rather than being consumed.</div>')

    sspension = sum(cur['incsrc'].get(c, 0) for c in SSPENSION_CATS)
    pension = sum(cur['incsrc'].get(c, 0) for c in PENSION_CATS)
    tax_est = sspension * TAX_RATE_FED + pension * TAX_RATE_CA
    net_guaranteed = cur['income'] - tax_est
    taxnote = (f'<div class="lsub">Shown above is gross. An estimated <b>~{usd(tax_est)}/yr</b> of that '
               f'is owed in tax on Social Security and pension income specifically (not withheld or '
               f'split out in the feed) — roughly <b>~{usd(net_guaranteed)}/yr</b> is actually available. '
               f'Rate ({TAX_RATE_FED*100:.1f}% federal / {TAX_RATE_CA*100:.1f}% CA) is an illustrative '
               f'example marginal bracket, not a fixed feature of SS/pension — it moves '
               f'with total income (especially realized capital gains) and should be re-derived from each '
               f'new return, not trusted indefinitely. Not a CPA figure — see config.py.</div>')

    dr = [(f"Routine draw — {usd(cur['routine_amt'])}/mo, annual total", cur['routine'], "var(--slate)"),
          ("Episodic draw — usually tax", cur['episodic'], "var(--gold)")]
    drjs = ','.join(f'["{html.escape(n)}",{round(v)},"{c}"]' for n, v, c in dr if v)

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Household Financial Summary — {html.escape(HOUSEHOLD_NAME)}</title>
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
.hero{{background:var(--hero);border-radius:18px;padding:32px 34px 8px;box-shadow:var(--shadow);overflow:hidden}}
.hero .verdict{{font-size:1.35rem;line-height:1.35;font-weight:500;max-width:34ch;color:#f2f6f2}}
.hero .nwrow{{display:flex;flex-wrap:wrap;align-items:flex-end;gap:8px 28px;margin:18px 0 6px}}
.hero .big{{font-size:clamp(2.4rem,6.5vw,3.7rem);line-height:.98;font-weight:600;letter-spacing:-.02em;color:#fff}}
.hero .chg{{color:#9db3a8;font-size:.92rem;padding-bottom:.5rem}}
.hero figcaption{{color:#8ea79b;font-size:.75rem;margin:2px 2px 14px}}
.hero figure{{margin:0}}
.trend{{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:22px 24px;
 box-shadow:var(--shadow);margin-top:20px}}
.trend h2{{font-size:1.02rem;font-weight:600;margin:0 0 3px}}
.trend .period{{color:var(--faint);font-size:.8rem;margin:5px 0 0;max-width:66ch;line-height:1.45}}
.trend .period b{{color:var(--muted)}}
.trend .lead{{color:var(--ink);font-size:.95rem;margin:12px 0 4px;max-width:64ch;line-height:1.5}}
.trend .lead b{{color:var(--green)}}
#trendchart{{margin-top:18px}}
.chartlegend{{display:flex;gap:18px;font-size:.76rem;color:var(--muted);margin-top:6px}}
.chartlegend span{{display:inline-flex;align-items:center;gap:6px}}
.chartlegend i{{width:9px;height:9px;border-radius:2px;display:inline-block}}
.ttable{{width:100%;border-collapse:collapse;margin-top:16px;font-variant-numeric:tabular-nums}}
.ttable th{{font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
 font-weight:600;text-align:right;padding:0 0 8px 12px}}
.ttable th:first-child{{text-align:left;padding-left:0}}
.ttable td{{padding:11px 0 11px 12px;border-top:1px solid var(--edge);text-align:right;font-size:1rem}}
.ttable td:first-child{{text-align:left;padding-left:0;font-size:.9rem;color:var(--muted)}}
.ttable td.now{{font-weight:600;font-size:1.08rem}}
.ttable .verdict{{font-size:.78rem;color:var(--muted);text-align:left;padding-left:14px;
 line-height:1.35;max-width:19ch}}
.ttable .good{{color:var(--green);font-weight:600}}
.ttable .flat{{color:var(--gold);font-weight:600}}
.layers{{display:flex;flex-direction:column;gap:14px;margin-top:20px}}
.layer{{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:20px 22px;
 box-shadow:var(--shadow);border-left:3px solid var(--accent,var(--edge))}}
.layer .lhead{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}}
.layer .ltitle{{font-size:1.02rem;font-weight:600}}
.layer .lper{{font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
 font-weight:600;margin-top:2px}}
.layers-head{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
 flex-wrap:wrap;margin:22px 0 -6px}}
.layers-head h2{{font-size:1.02rem;font-weight:600;margin:0}}
.layers-head .sub{{color:var(--muted);font-size:.82rem}}
.layer .lnum{{font-size:1.5rem;font-weight:600;letter-spacing:-.01em}}
.layer .lnote{{color:var(--muted);font-size:.85rem;margin-top:6px;max-width:66ch}}
.layer .lsub{{font-size:.82rem;margin-top:9px;max-width:70ch;color:var(--muted);background:var(--paper);
 border:1px solid var(--edge);border-left:2px solid var(--green);border-radius:8px;padding:9px 13px}}
.layer .lsub b{{color:var(--ink);font-weight:600}}
.lrows{{display:flex;flex-direction:column;gap:9px;margin-top:14px}}
.lrow{{display:grid;grid-template-columns:1fr auto;gap:3px 12px;align-items:center}}
.lrow .rl{{font-size:.87rem}} .lrow .rv{{font-size:.87rem;color:var(--muted);text-align:right}}
.lrow .track{{grid-column:1/-1;height:8px;border-radius:4px;background:var(--grid);overflow:hidden}}
.lrow .fill{{height:100%;border-radius:4px;transition:width .9s cubic-bezier(.2,.7,.2,1)}}
.neg{{color:var(--clay)}}
.gapline{{margin-top:14px;padding-top:13px;border-top:1px solid var(--edge);display:flex;
 justify-content:space-between;align-items:baseline;gap:10px;font-weight:600}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px}}
.mini{{background:var(--paper);border:1px solid var(--edge);border-radius:12px;padding:16px 18px}}
.mini h3{{font-size:.82rem;font-weight:600;margin:0 0 10px;color:var(--muted)}}
.mini .row{{display:flex;justify-content:space-between;gap:10px;font-size:.86rem;padding:3px 0}}
.mini .row .v{{color:var(--muted)}}
.mini .tot{{border-top:1px solid var(--edge);margin-top:7px;padding-top:8px;font-weight:600}}
.mini .tot .v{{color:var(--ink)}}
.note{{background:var(--card);border:1px solid var(--edge);border-left:3px solid var(--slate);
 border-radius:12px;padding:18px 22px;margin-top:14px;box-shadow:var(--shadow)}}
.note h2{{font-size:.98rem;margin:0 0 8px}}
.note p{{margin:0 0 9px;font-size:.9rem}} .note p:last-child{{margin-bottom:0}}
.note .q{{color:var(--muted);font-size:.84rem}}
footer{{color:var(--faint);font-size:.76rem;margin-top:28px;display:flex;justify-content:space-between;
 flex-wrap:wrap;gap:8px}}
.tag{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--edge);border-radius:999px;
 padding:3px 10px;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}}
#tt{{position:fixed;pointer-events:none;background:var(--hero);color:#eef3ef;border-radius:8px;padding:7px 10px;
 font-size:.8rem;opacity:0;transition:opacity .12s;z-index:9;white-space:nowrap}}
#tt .t2{{color:#9db3a8;font-size:.74rem}}
svg{{display:block;width:100%;height:auto}}
@media (max-width:780px){{.grid2{{grid-template-columns:1fr}}}}
@media (max-width:640px){{.ttable .verdict{{display:none}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">{html.escape(HOUSEHOLD_NAME)}</h1>
        <div class="meta">12 months to {cur['end'].strftime('%B %Y')}</div>
      </div>
      <span class="tag">● Private</span>
    </div>
    <nav class="topnav">
      <a href="dashboard.html" class="here">Financial Summary</a>
      <a href="spending.html">Spending by Category</a>
      <a href="tiers.html">Spending by Tier</a>
      <a href="downsize.html">Downsize Model</a>
      <a href="sustainability.html">Retirement Sustainability</a>
    </nav>
  </header>
  <div class="rule"></div>

  <section class="hero">
    <div class="verdict serif">Our net worth {'held steady' if abs(nwpct) < 3 else ('rose' if nwpct > 0 else 'fell')} over the past year.</div>
    <div class="nwrow">
      <div class="big serif num">${latest/1e6:.2f}M</div>
      <div class="chg num">financial net worth · {nwpct:+.1f}% vs. a year ago</div>
    </div>
    <figure><div id="nwchart"></div>
      <figcaption>Month-end net worth · {nw[0][0].strftime('%b %Y')} – {nw[-1][0].strftime('%b %Y')} · excludes home equity</figcaption>
    </figure>
  </section>

  <section class="trend">
    <h2 class="serif">Overview — the direction of travel over three years</h2>
    <p class="period">Each column is a complete 12 months, and they roll forward as time passes —
      the current one ends <b>{cur['end'].strftime('%d %B %Y')}</b>. They are not calendar years, so that
      all three are full periods.</p>
    <p class="lead">{lead}</p>
    <div id="trendchart"></div>
    <div class="chartlegend">
      <span><i style="background:var(--green)"></i>Guaranteed income</span>
      <span><i style="background:var(--slate)"></i>Portfolio draw</span>
      <span><i style="background:var(--clay)"></i>Lifestyle spending</span>
    </div>
    <table class="ttable">
      <thead><tr><th></th><th>{hdr(W[0])}</th><th>{hdr(W[1])}</th><th>{hdr(W[2])}</th><th class="verdict"></th></tr></thead>
      <tbody>
        <tr><td>What our life costs</td><td>{k(life[0])}</td><td>{k(life[1])}</td><td class="now">{k(life[2])}</td>
            <td class="verdict">{verdict(life[0], life[2], note=f['why'].get('life'))}</td></tr>
        <tr><td>Income we can count on</td><td>{k(inc[0])}</td><td>{k(inc[1])}</td><td class="now">{k(inc[2])}</td>
            <td class="verdict">{verdict(inc[0], inc[2], good_down=False, note=f['why'].get('income'))}</td></tr>
        <tr><td>Monthly draw from savings</td><td>{k(rate[0])}</td><td>{k(rate[1])}</td><td class="now">{k(rate[2])}</td>
            <td class="verdict">{verdict(rate[0], rate[2], note=f['why'].get('draw'))}</td></tr>
        <tr><td>Shortfall the portfolio covers</td><td>{k(gap[0])}</td><td>{k(gap[1])}</td><td class="now">{k(gap[2])}</td>
            <td class="verdict">{verdict(gap[0], gap[2], note=f"{usd(abs(gap[2]-gap[0]))} better")}</td></tr>
      </tbody>
    </table>
  </section>

  <div class="layers-head">
    <h2 class="serif">The last 12 months in detail</h2>
    <div class="sub">{cur['start'].strftime('%B %Y')} – {cur['end'].strftime('%B %Y')} · the three sections
      below all cover this single 12-month period, not the three-year view above</div>
  </div>

  <div class="layers">
    <section class="layer" style="--accent:var(--green)">
      <div class="lhead"><div>
          <div class="ltitle serif">1 · Household — what our life costs</div>
          <div class="lper">12 months to {cur['end'].strftime('%b %Y')}</div></div>
        <div class="lnum num">{usd(abs(cur['life']))}</div></div>
      <div class="lnote">Our guaranteed income covers about {cur['income']/abs(cur['life'])*100:.0f}% of what we spend.
        The rest is funded by our routine {usd(cur['routine_amt'])}/month portfolio draw. Taxes are
        excluded here — they belong to layer 2.</div>
      {mortnote}
      <div class="grid2">
        <div class="mini"><h3>Our guaranteed income</h3><div id="incrows"></div></div>
        <div class="mini"><h3>What we spend</h3><div class="lrows" id="exprows"></div></div>
      </div>
      {taxnote}
      <div class="gapline"><span>Gap funded by our portfolio</span>
        <span class="neg num">−{usd(abs(cur['gap']))} · −{usd(abs(cur['gap'])/12)}/mo</span></div>
    </section>

    <section class="layer" style="--accent:var(--gold)">
      <div class="lhead"><div>
          <div class="ltitle serif">2 · Taxes — a cost of income and portfolio returns</div>
          <div class="lper">calendar year {last_tax_year}</div></div>
        <div class="lnum num">{usd(last_year_tax_total)}</div></div>
      <div class="lnote">Not part of what we spend on ourselves. The bill is driven by interest, dividends and realised
        gains inside our portfolio — it would exist even if we spent nothing. We pay it with dedicated withdrawals, which is why it inflates both sides of a naive cash-flow
        statement. Shown by calendar year, not the rolling window used elsewhere on this page — taxes are actually owed on a
        calendar-year basis, so slotting them into a fiscal window was misleading.</div>
      <div class="grid2">
        <div class="mini"><h3>The tax we paid in for {last_tax_year}</h3>
          <div class="row"><span>Federal (IRS)</span><span class="v num">{usd(last_year_irs)}</span></div>
          <div class="row"><span>California (FTB)</span><span class="v num">{usd(last_year_ca)}</span></div>
          <div class="row tot"><span>Total</span><span class="v num">{usd(last_year_tax_total)}</span></div></div>
        <div class="mini"><h3>Why it swings</h3>
          {tax_year_rows}
          <div class="row tot"><span>Three-year average</span>
            <span class="v num">{usd(sum(tax_year_totals)/3)}</span></div></div>
      </div>
      {taxyear_note}
    </section>

    <section class="layer" style="--accent:var(--slate)">
      <div class="lhead"><div>
          <div class="ltitle serif">3 · Portfolio — the engine</div>
          <div class="lper">12 months to {cur['end'].strftime('%b %Y')}</div></div>
        <div class="lnum num">{usd(cur['draw'])} drawn</div></div>
      <div class="lnote">Our draw splits by purpose. Investment returns over the period replaced what we
        withdrew, which is why our net worth is roughly unchanged.</div>
      <div class="lrows" id="drawrows"></div>
      <div class="gapline"><span>Net worth, start → end</span>
        <span class="num">${yr_ago/1e6:.2f}M → ${latest/1e6:.2f}M</span></div>
    </section>
  </div>

  <section class="note">
    <h2 class="serif">How to read this</h2>
    <p>Our portfolio draw is <b>not income</b> — it is our own money moving between accounts. Counting it
      as income double-counts, because the matching fall in our portfolio balance is invisible at the
      transaction level.</p>
    <p>Our scorecard is <b>net worth</b>, which nets our spending, our draws, and the investment returns
      that never appear as transactions.</p>
    <p class="q">These figures exclude our home equity — our house is not tracked as an asset, only its mortgage
      as a liability. Whether this pace is sustainable over a 20–30 year retirement is a question for our advisor;
      this page shows the picture, not a verdict.</p>
  </section>

  <footer><span>Generated from the Tiller sheet · {f['generated'].strftime('%d %B %Y, %H:%M')}</span>
    <span>Private summary — not financial advice</span></footer>
</div>
<div id="tt"><div class="t1"></div><div class="t2"></div></div>
<script>
const fmt=n=>"$"+Math.round(n).toLocaleString();
const tt=document.getElementById('tt');
function showTip(x,y,a,b){{tt.querySelector('.t1').textContent=a;tt.querySelector('.t2').textContent=b;
 tt.style.opacity=1;const r=tt.getBoundingClientRect();
 tt.style.left=Math.min(Math.max(x-r.width/2,8),innerWidth-r.width-8)+'px';tt.style.top=(y-r.height-14)+'px';}}
function hideTip(){{tt.style.opacity=0;}}
function bindTips(sel){{document.querySelectorAll(sel).forEach(b=>{{
 b.addEventListener('pointermove',e=>showTip(e.clientX,b.getBoundingClientRect().top,b.dataset.b,b.dataset.a));
 b.addEventListener('pointerleave',hideTip);
 b.addEventListener('focus',()=>{{const r=b.getBoundingClientRect();showTip(r.left+r.width/2,r.top,b.dataset.b,b.dataset.a);}});
 b.addEventListener('blur',hideTip);}});}}
const nw=[{nwjs}];
(function(){{
 const W=980,H=210,P={{l:8,r:8,t:18,b:24}},yMin={ymin:.0f},yMax={ymax:.0f};
 const cs=getComputedStyle(document.documentElement);
 const green=cs.getPropertyValue('--green').trim(),gold=cs.getPropertyValue('--gold').trim();
 const X=i=>P.l+(W-P.l-P.r)*i/(nw.length-1), Y=v=>P.t+(H-P.t-P.b)*(1-(v-yMin)/(yMax-yMin));
 let g='';const step=(yMax-yMin)/4;
 for(let i=0;i<=4;i++){{const v=yMin+step*i,y=Y(v);
  g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="#2a3730" stroke-width="1"/>`+
     `<text x="${{P.l}}" y="${{y-4}}" fill="#8ea79b" font-size="11">$${{(v/1e6).toFixed(2)}}M</text>`;}}
 const yb=Y(nw[0][1]);
 g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{yb}}" y2="${{yb}}" stroke="${{gold}}" stroke-width="1" stroke-dasharray="3 4" opacity=".55"/>`;
 const pts=nw.map((d,i)=>[X(i),Y(d[1])]);
 const line=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
 const area=line+` L${{W-P.r}} ${{H-P.b}} L${{P.l}} ${{H-P.b}} Z`;const last=pts[pts.length-1];
 document.getElementById('nwchart').innerHTML=
  `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none" role="img" aria-label="Month-end net worth">
   <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1">
   <stop offset="0" stop-color="${{green}}" stop-opacity=".30"/><stop offset="1" stop-color="${{green}}" stop-opacity="0"/>
   </linearGradient></defs>${{g}}<path d="${{area}}" fill="url(#ga)"/>
   <path d="${{line}}" fill="none" stroke="${{green}}" stroke-width="2.5" stroke-linejoin="round"/>
   <circle cx="${{last[0]}}" cy="${{last[1]}}" r="4.5" fill="${{gold}}" stroke="#16211d" stroke-width="2"/>
   <g id="hit"></g></svg>`;
 const svg=document.querySelector('#nwchart svg'),hit=svg.querySelector('#hit');
 const cross=document.createElementNS('http://www.w3.org/2000/svg','line');
 cross.setAttribute('stroke',gold);cross.setAttribute('stroke-width','1');cross.setAttribute('opacity','0');
 cross.setAttribute('y1',P.t);cross.setAttribute('y2',H-P.b);hit.appendChild(cross);
 const mk=document.createElementNS('http://www.w3.org/2000/svg','circle');
 mk.setAttribute('r','5');mk.setAttribute('fill',gold);mk.setAttribute('stroke','#16211d');
 mk.setAttribute('stroke-width','2');mk.setAttribute('opacity','0');hit.appendChild(mk);
 svg.addEventListener('pointermove',e=>{{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*W;
  let i=Math.round((px-P.l)/((W-P.l-P.r)/(nw.length-1)));i=Math.max(0,Math.min(nw.length-1,i));
  const p=pts[i];cross.setAttribute('x1',p[0]);cross.setAttribute('x2',p[0]);cross.setAttribute('opacity','.6');
  mk.setAttribute('cx',p[0]);mk.setAttribute('cy',p[1]);mk.setAttribute('opacity','1');
  showTip(r.left+p[0]/W*r.width,r.top+p[1]/H*r.height,fmt(nw[i][1]),nw[i][0]);}});
 svg.addEventListener('pointerleave',()=>{{cross.setAttribute('opacity','0');mk.setAttribute('opacity','0');hideTip();}});
}})();
const trend=[{trendjs}];
(function(){{
 const W=980,H=220,P={{l:44,r:8,t:10,b:28}};
 const cs=getComputedStyle(document.documentElement);
 const green=cs.getPropertyValue('--green').trim(),clay=cs.getPropertyValue('--clay').trim(),
       slate=cs.getPropertyValue('--slate').trim(),edge=cs.getPropertyValue('--edge').trim(),
       faint=cs.getPropertyValue('--faint').trim(),muted=cs.getPropertyValue('--muted').trim();
 const series=[['Guaranteed income',green,1],['Portfolio draw',slate,2],['Lifestyle spending',clay,3]];
 const max=Math.max(...trend.flatMap(d=>[d[1],d[2],d[3]]));
 const Y=v=>P.t+(H-P.t-P.b)*(1-v/max);
 const groupW=(W-P.l-P.r)/trend.length, barW=groupW/(series.length+1.4);
 let g='';const step=max/4;
 for(let i=0;i<=4;i++){{const v=step*i,y=Y(v);
  g+=`<line x1="${{P.l}}" x2="${{W-P.r}}" y1="${{y}}" y2="${{y}}" stroke="${{edge}}" stroke-width="1"/>`+
     `<text x="${{P.l-8}}" y="${{y+4}}" fill="${{faint}}" font-size="11" text-anchor="end">$${{Math.round(v/1000)}}k</text>`;}}
 trend.forEach((d,gi)=>{{
  const gx=P.l+groupW*gi;
  series.forEach(([label,color,idx])=>{{
   const v=d[idx],y=Y(v),h=(H-P.b)-y,x=gx+barW*(idx-0.5);
   g+=`<rect class="tbar" tabindex="0" data-a="${{label}}" data-b="${{fmt(v)}}" x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" `+
      `width="${{(barW*.82).toFixed(1)}}" height="${{h.toFixed(1)}}" fill="${{color}}" rx="2"/>`;}});
  g+=`<text x="${{(gx+groupW/2).toFixed(1)}}" y="${{H-8}}" fill="${{muted}}" font-size="11.5" text-anchor="middle">${{d[0]}}</text>`;}});
 document.getElementById('trendchart').innerHTML=
  `<svg viewBox="0 0 ${{W}} ${{H}}" preserveAspectRatio="none" role="img" aria-label="Three-year income, spending and draw">${{g}}</svg>`;
 bindTips('.tbar');
}})();
const inc=[{incrows}];
document.getElementById('incrows').innerHTML=
 inc.map(d=>`<div class="row"><span>${{d[0]}}</span><span class="v num">${{fmt(d[1])}}</span></div>`).join('')
 +`<div class="row tot"><span>Total guaranteed</span><span class="v num">{usd(cur['income'])}</span></div>`;
const ex=[{bars}];
(function(){{const max=Math.max(...ex.map(d=>d[1]));
 document.getElementById('exprows').innerHTML=ex.map(d=>`
  <div class="lrow" tabindex="0" data-a="${{d[0]}}" data-b="${{fmt(d[1])}}">
   <span class="rl">${{d[0]}}</span><span class="rv num">${{fmt(d[1])}}</span>
   <span class="track"><span class="fill" style="width:0;background:var(--green)"></span></span></div>`).join('');
 requestAnimationFrame(()=>document.querySelectorAll('#exprows .fill').forEach((f,i)=>f.style.width=(ex[i][1]/max*100)+'%'));
 bindTips('#exprows .lrow');}})();
const dr=[{drjs}];
(function(){{const max=dr.reduce((a,b)=>a+b[1],0)||1;
 document.getElementById('drawrows').innerHTML=dr.map(d=>`
  <div class="lrow" tabindex="0" data-a="${{d[0]}}" data-b="${{fmt(d[1])}}">
   <span class="rl">${{d[0]}}</span><span class="rv num">${{fmt(d[1])}}</span>
   <span class="track"><span class="fill" style="width:0;background:${{d[2]}}"></span></span></div>`).join('')
  +`<div class="gapline" style="border-top:none;padding-top:4px;margin-top:2px"><span>Total drawn</span>
    <span class="num">{usd(cur['draw'])}</span></div>`;
 requestAnimationFrame(()=>document.querySelectorAll('#drawrows .fill').forEach((f,i)=>f.style.width=(dr[i][1]/max*100)+'%'));
 bindTips('#drawrows .lrow');}})();
</script>
</body>
</html>'''
    return T

if __name__ == '__main__':
    f = compute()
    open(OUT, 'w').write(build_html(f))
    cur = f['cur']
    print(f'wrote {OUT}')
    print(f"  window {cur['start']} .. {cur['end']}")
    print(f"  income {cur['income']:,.0f} | draw {cur['draw']:,.0f} "
          f"(routine {cur['routine']:,.0f} @ {cur['routine_amt']:,.0f}/mo) "
          f"| lifestyle {abs(cur['life']):,.0f} | gap {cur['gap']:,.0f}")
