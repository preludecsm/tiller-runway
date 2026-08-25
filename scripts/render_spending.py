#!/usr/bin/env python3
"""
render_spending.py — regenerate reports/spending.html: spending trend by bucket and category.

Same three rolling 12-month windows as render_dashboard.py (last 12 complete months, and the
two preceding 12-month blocks), broken down by bucket and, within each bucket, by category.

  python3 render_spending.py

One-off / volatile categories are flagged, not hand-picked: a category is flagged when one
window's spend is more than 2.2x the average of the other two (and the swing is at least
$1,500), so a category that starts, stops, or spikes shows up automatically and the flag
cannot go stale the way a hardcoded list would — this is what turns "lumpy one-offs" from a
concept into a computed list.

Every category row is clickable — expands in place to the actual transactions behind it
(date, description, amount), full 3-year window, newest first, matching the totals shown above
it. All transaction data for shown categories is embedded in the page (no backend), but each
category's list is only built in the DOM when first opened, so the page stays fast to load even
though the underlying dataset is a few thousand rows.
"""
import gspread, glob, os, re, html, json, sys
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROJECT_ROOT, SHEET_ID, SHEET_NAME, HOUSEHOLD_NAME

P = PROJECT_ROOT; os.chdir(P)
OUT = f'{P}/reports/spending.html'

TAXCATS = ('IRS', 'CA Tax')
FLAT_PCT = 8
ONEOFF_RATIO = 2.2      # a window at least this many times the other two's average
ONEOFF_MIN = 1500        # ...and the swing must be at least this many dollars to matter

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
        rows.append((d, r[CA].strip(), a, r[DE].strip()))

    today = date.today()
    end = monthstart(today) - timedelta(days=1)
    w0_start = addmonths(monthstart(end), -11)
    wins = []
    for i in range(3):
        st = addmonths(w0_start, -12*i)
        en = addmonths(st, 12) - timedelta(days=1)
        wins.append((st, en))
    wins.reverse()

    def agg(st, en):
        cat = defaultdict(float)
        for d, c, a, de in rows:
            if not (st <= d <= en): continue
            if ctype.get(c, '') == 'Expense' and c not in TAXCATS:
                cat[c] += a
        return dict(start=st, end=en, cat=cat)

    W = [agg(st, en) for st, en in wins]

    # every expense category (excluding tax) seen in any window, grouped by the Categories tab's
    # Bucket column — a category with a blank Bucket falls to "Other" rather than being dropped,
    # so a newly-added category can't silently disappear from the page before someone tags it
    all_cats = set()
    for w in W: all_cats.update(w['cat'])
    by_bucket = defaultdict(list)
    for c in all_cats:
        by_bucket[cbucket.get(c, '') or 'Other'].append(c)
    groups = sorted(by_bucket.items(), key=lambda kv: (kv[0] == 'Other', kv[0]))

    def series(c):
        return [round(-W[i]['cat'].get(c, 0)) for i in range(3)]

    def flag(vals3):
        a, b, c = vals3
        # started recently: negligible in the first two years, material in the latest
        if c >= ONEOFF_MIN and a < ONEOFF_MIN / 2 and b < ONEOFF_MIN / 2:
            return ('new', 2)
        # wound down: material in the first year, negligible by the latest — the middle year
        # can be transitional (a multi-year wind-down, not a step function)
        if a >= ONEOFF_MIN and c < ONEOFF_MIN / 2:
            return ('stopped', 0)
        # otherwise: any single year far out of line with the other two
        for i, v in enumerate(vals3):
            others = [vals3[j] for j in range(3) if j != i]
            avg = sum(others) / 2
            swing = abs(v - avg)
            if swing < ONEOFF_MIN: continue
            if avg == 0 or v / avg >= ONEOFF_RATIO or v <= avg / ONEOFF_RATIO:
                return ('volatile', i)
        return None

    bucket_rows = []
    for name, cs in groups:
        present = [c for c in cs if any(W[i]['cat'].get(c) for i in range(3))]
        if not present: continue
        btotal = [sum(series(c)[i] for c in present) for i in range(3)]
        cat_rows = []
        for c in sorted(present, key=lambda c: -series(c)[2]):
            s = series(c)
            cat_rows.append(dict(name=c, vals=s, flag=flag(s)))
        bucket_rows.append(dict(name=name, vals=btotal, cats=cat_rows))
    bucket_rows.sort(key=lambda b: -b['vals'][2])

    # flagged one-off categories, for the "with one-offs removed" line
    oneoff_cats = [c['name'] for b in bucket_rows for c in b['cats'] if c['flag']]
    life = [sum(-W[i]['cat'].get(c, 0) for c in all_cats) for i in range(3)]
    life_ex = [sum(-W[i]['cat'].get(c, 0) for c in all_cats if c not in oneoff_cats) for i in range(3)]

    # underlying transactions per category, full 3-year window, newest first — for the
    # click-to-expand drill-down. Only collected for categories actually shown (present in some
    # bucket), so a category that never appears never pays for a transaction list.
    shown_cats = {c['name'] for b in bucket_rows for c in b['cats']}
    txns = defaultdict(list)
    win_start, win_end = W[0]['start'], W[2]['end']
    for d, c, a, de in rows:
        if c not in shown_cats or not (win_start <= d <= win_end): continue
        txns[c].append((d, de, a))
    for c in txns: txns[c].sort(key=lambda t: t[0], reverse=True)

    return dict(titles=titles, W=W, buckets=bucket_rows, life=life, life_ex=life_ex,
                oneoff_cats=oneoff_cats, txns=txns, generated=datetime.now())

def verdict(a, b, good_down=True):
    ch = (b - a) / a * 100 if a else 0
    if abs(ch) < FLAT_PCT:
        return '<span class="flat">Flat</span>'
    down = ch < 0
    cls = 'good' if (down == good_down) else 'flat'
    word = ('Down' if down else 'Up') + f' {abs(ch):.0f}%'
    return f'<span class="{cls}">{word}</span>'

FLAG_LABEL = {'new': 'new', 'stopped': 'stopped', 'volatile': 'volatile'}

def build_html(f):
    nav = nav_html('spending.html', f['titles']); partial_note = missing_note(f['titles'])
    W = f['W']
    def hdr(w): return f"{w['start'].strftime('%b ’%y')}–{w['end'].strftime('%b ’%y')}"
    headers = [hdr(w) for w in W]

    def bucket_group(b, idx):
        bid = f'b{idx}'
        cat_rows = ''.join(
            f'<tr class="catrow" hidden data-bucket="{bid}" tabindex="0" data-cat="{html.escape(c["name"], quote=True)}">'
            f'<td>{html.escape(c["name"])}'
            + (f' <span class="flagtag {c["flag"][0]}">{FLAG_LABEL[c["flag"][0]]}</span>' if c['flag'] else '')
            + f'</td><td class="num">{usd(c["vals"][0])}</td><td class="num">{usd(c["vals"][1])}</td>'
              f'<td class="num now">{usd(c["vals"][2])}</td></tr>'
              f'<tr class="txrow" hidden data-bucket="{bid}"><td colspan="4"><div class="txwrap"><div class="txlist"></div></div></td></tr>'
            for c in b['cats'])
        return f'''
      <tr class="bucketrow" tabindex="0" data-toggle="{bid}">
        <td>{html.escape(b['name'])}</td>
        <td class="num">{k(b['vals'][0])}</td><td class="num">{k(b['vals'][1])}</td>
        <td class="num now"><b>{k(b['vals'][2])}</b></td></tr>
{cat_rows}'''

    buckets_html = ''.join(bucket_group(b, i) for i, b in enumerate(f['buckets']))

    txns_json = json.dumps({
        cat: [[f'{d.month}/{d.day}/{d.year}', de, round(a, 2)] for d, de, a in lst]
        for cat, lst in f['txns'].items()
    }, separators=(',', ':'))

    life, life_ex = f['life'], f['life_ex']
    oneoffs = f['oneoff_cats']
    oneoff_note = (f'<p class="period">With the {len(oneoffs)} most volatile categories removed '
                   f'({", ".join(html.escape(c) for c in oneoffs)}), the underlying cost of living '
                   f'trend is {k(life_ex[0])} → {k(life_ex[1])} → {k(life_ex[2])}.</p>'
                   if oneoffs else '<p class="period">No category swung more than 2.2x its other two years by at least $1,500 — nothing flagged as a one-off.</p>')

    T = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spending by Category — {html.escape(HOUSEHOLD_NAME)}</title>
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
.spendtable{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-top:18px}}
.spendtable th{{font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
 font-weight:600;text-align:right;padding:0 18px 8px;position:sticky;top:0;background:var(--paper)}}
.spendtable th:first-child{{text-align:left}}
.spendtable td{{padding:8px 18px;border-top:1px solid var(--edge);text-align:right;font-size:.88rem}}
.spendtable td:first-child{{text-align:left;color:var(--muted)}}
.spendtable td.now{{font-weight:600;color:var(--ink)}}
.bucketrow{{cursor:pointer;background:var(--card)}}
.bucketrow td{{font-weight:600;color:var(--ink);font-size:.92rem;padding-top:12px;padding-bottom:12px}}
.bucketrow:hover td{{background:var(--paper)}}
.bucketrow:focus-visible{{outline:2px solid var(--gold);outline-offset:-2px}}
.bucketrow td:first-child:before{{content:'\\25B8';display:inline-block;width:1em;margin-left:-1em;
 color:var(--faint);transition:transform .15s}}
.bucketrow.open td:first-child:before{{transform:rotate(90deg)}}
.catrow td:first-child{{padding-left:38px}}
.flagtag{{display:inline-block;font-size:.62rem;letter-spacing:.04em;text-transform:uppercase;
 font-weight:700;border-radius:5px;padding:1px 6px;margin-left:6px;vertical-align:middle}}
.flagtag.new{{background:rgba(47,109,84,.15);color:var(--green)}}
.flagtag.stopped{{background:rgba(169,80,59,.15);color:var(--clay)}}
.flagtag.volatile{{background:rgba(143,111,52,.18);color:var(--gold)}}
.good{{color:var(--green);font-weight:600}}
.flat{{color:var(--gold);font-weight:600}}
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
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">Household Financial Summary</div>
        <h1 class="serif">Spending by Category</h1>
        <div class="meta">12 months to {W[2]['end'].strftime('%B %Y')}</div>
      </div>
      <span class="tag">● Demo data</span>
    </div>
    <nav class="topnav">
      {nav}
    </nav>
  </header>
  <div class="rule"></div>

  <section class="trend">
    <h2 class="serif">Lifestyle spending, by bucket and category</h2>
    <p class="period">Same three rolling 12-month windows as the Financial Summary (Jul–Jun, so all
      three are complete years) — the current one ends <b>{W[2]['end'].strftime('%d %B %Y')}</b>.
      Taxes are excluded, same as the summary. Click a bucket to see the categories inside it.
      A category tagged <b>new</b>, <b>stopped</b> or <b>volatile</b> moved more than 2.2x its other two
      years by at least $1,500 — computed from the data, not a hand-picked list.</p>
    <p class="period">Total lifestyle spending: {k(life[0])} → {k(life[1])} → {k(life[2])}
      &nbsp;({verdict(life[0], life[2])})</p>
    {oneoff_note}
    <table class="spendtable">
      <thead><tr><th>Bucket / Category</th><th>{headers[0]}</th><th>{headers[1]}</th><th>{headers[2]}</th></tr></thead>
      <tbody>{buckets_html}</tbody>
    </table>
  </section>

  {partial_note}
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
document.querySelectorAll('.bucketrow').forEach(brow=>{{
 const id=brow.dataset.toggle;
 const rows=[...document.querySelectorAll(`tr[data-bucket="${{id}}"]`)];
 const toggle=()=>{{
  const opening=brow.classList.toggle('open');
  rows.forEach(r=>{{
   if(r.classList.contains('catrow')) r.hidden=!opening;
   else r.hidden=!(opening && r.previousElementSibling.classList.contains('open'));
  }});
 }};
 brow.addEventListener('click',toggle);
 brow.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();toggle();}}}});
}});
</script>
</body>
</html>'''
    return T

if __name__ == '__main__':
    f = compute()
    open(OUT, 'w').write(build_html(f))
    print(f'wrote {OUT}')
    print(f"  lifestyle spending: {f['life'][0]:,.0f} -> {f['life'][1]:,.0f} -> {f['life'][2]:,.0f}")
    print(f"  {len(f['buckets'])} buckets, {len(f['oneoff_cats'])} categories flagged: {f['oneoff_cats']}")
