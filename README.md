# Tiller Runway

Retirement budgeting built on top of a [Tiller](https://tiller.com) Google Sheet — which spending
to cut first if income drops, whether selling the house and renting instead would actually work,
and how long the whole portfolio can sustain what you spend today.

### [→ See it running, no install or account needed](https://preludecsm.github.io/tiller-runway/)

[The live demo](https://preludecsm.github.io/tiller-runway/), the
[sample PDF](docs/advisor_summary.pdf), and the
[sample Google Sheet](https://docs.google.com/spreadsheets/d/1-pdkeSihXshh_2BB8BLRRpAGoAKfII8XK6R6Us3xAaY/edit)
all run on a made-up household ("Alex & Jordan") — every figure is fabricated, so there is nothing
real to protect. See [`generate_demo_data.py`](scripts/generate_demo_data.py) for how it's built.
Full-page screenshots of all five reports are below, so you can see exactly what you're getting
into before copying a sheet or running any code.

**Not financial, tax, or legal advice.** Every figure here is a model output based on assumptions
you control — verify independently before making any real decision. Provided as-is, with no
warranty; see [`LICENSE`](LICENSE).

## What's here that you won't find in a typical Tiller dashboard

1. **Cut-order tier budgeting.** Every spending category is tagged Tier 0 (floor, never cut)
   through Tier 3 (purely elective) on the sheet itself, and a funding-source waterfall shows
   exactly which pool — guaranteed income, routine portfolio draw, or episodic draw — is paying
   for each tier, in priority order.

2. **A downsize/sale-to-rent Monte Carlo model.** "If we sold the house today, could the invested
   proceeds sustainably cover rent?" — with a real mortgage-interest/property-tax tax-benefit
   calculator netted against the raw cost of owning, not just a rule-of-thumb comparison.

3. **A whole-portfolio retirement-sustainability model**, a different question from #2: given
   your *entire* net worth plus guaranteed income, what can you actually sustain spending,
   at what confidence level, over 20/25/30/50-year horizons?

4. **A separate, aggregate-only PDF generator.** The interactive pages embed full per-category
   transaction detail for click-to-expand charts — great for you, not something to hand a third
   party. `render_advisor_pdf.py` builds an 8-page PDF from rollups only (net worth, tier
   composition, bucket totals) — no individual transaction ever appears — safe to send to a CPA
   or financial advisor.

Every assumption (tax rates, Monte Carlo trial count, inflation, LTCG rate, ...) lives in an
editable cell on the Google Sheet, never hardcoded in the scripts — change a number, re-run, done.

## Every page, in full

Complete top-to-bottom screenshots of all five report pages, exactly as the
[live demo](https://preludecsm.github.io/tiller-runway/) serves them. Each caption links to the
live, clickable version.

### [Financial Summary](https://preludecsm.github.io/tiller-runway/dashboard.html)
Net worth and a three-year income/spending trend, with guaranteed income kept separate from the
portfolio draw.

![Full Financial Summary page](docs/img/full-dashboard.png)

### [Spending by Category](https://preludecsm.github.io/tiller-runway/spending.html)
Three years of spending grouped into buckets — on the live page each bucket clicks open to the
categories inside it, and unusual categories are flagged computationally, not hand-picked.

![Full Spending by Category page](docs/img/full-spending.png)

### [Spending by Tier](https://preludecsm.github.io/tiller-runway/tiers.html)
Every expense ranked by how easily it could be cut, and the funding waterfall showing which
income pool pays for which tier, in priority order.

![Full Spending by Tier page](docs/img/full-tiers.png)

### [Downsize / Sale-to-Rent Model](https://preludecsm.github.io/tiller-runway/downsize.html)
The net-proceeds waterfall, the sustainable-rent Monte Carlo, and the tax-benefit-of-owning
calculation, with every assumption listed at the bottom — all editable on the sheet.

![Full Downsize Model page](docs/img/full-downsize.png)

### [Retirement Sustainability](https://preludecsm.github.io/tiller-runway/sustainability.html)
What the whole portfolio plus guaranteed income can sustainably support, at what confidence,
over 20/25/30/50-year horizons — with today's actual spending marked against the curve.

![Full Retirement Sustainability page](docs/img/full-sustainability.png)

And the [sample advisor PDF](docs/advisor_summary.pdf) — the same numbers rolled up with no
individual transactions, safe to hand to a CPA or financial advisor.

## Two ways to try this yourself

**1. Look around — no data exposure.** The five report pages above are real and clickable, and
the [sample sheet](https://docs.google.com/spreadsheets/d/1-pdkeSihXshh_2BB8BLRRpAGoAKfII8XK6R6Us3xAaY/edit)
is open to view — **File → Make a copy** gets you your own private copy of the raw structure to
poke at, with nothing to install and nothing of yours involved.

**2. Run it yourself, against your own real Tiller sheet — limited, reversible exposure.** Clone
this repo and follow [`SETUP_GOOGLE_SHEETS.md`](SETUP_GOOGLE_SHEETS.md) to create a free Google
"service account" — a robot identity Google lets you create so a program (not a person) can read
one specific sheet, the same way you'd share a sheet with a coworker's email. What that exposes:
that one sheet, nothing else in your Google account. The one thing to handle carefully is the
downloadable key file Google gives you for it — unlike a password, whoever holds that file can use
it with no login prompt, so the habit that keeps it safe is simply *never let it leave your own
computer* (don't email it, don't put it in a synced folder). If it's ever lost, one click in the
Google Cloud console revokes it instantly. See **Security** below for the full explanation.

## Quickstart (using the synthetic demo data)

```bash
git clone https://github.com/preludecsm/tiller-runway.git
cd tiller-runway
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Then set up a Google Sheet and share it with a service account — see
[`SETUP_GOOGLE_SHEETS.md`](SETUP_GOOGLE_SHEETS.md) for the walkthrough (it's a one-time thing; a
service account can't create its own sheet, so you make a blank one and share it). With only
synthetic data involved at this stage there's nothing sensitive to worry about yet, but the doc
also covers exactly what "sharing a sheet with a service account" does and doesn't expose, worth
reading before you ever point this at real data — see **Security** below.

```bash
.venv/bin/python3 scripts/generate_demo_data.py   # fabricates 3 years of synthetic household data
./run_demo.sh                                     # renders all 6 reports
open reports/dashboard.html                       # or reports/advisor_summary.pdf
```

## Using your own real data

**Where the reports actually live:** not in your sheet. The scripts *read* your sheet (they never
write to it), do the math on your computer, and save ordinary `.html` files into the `reports/`
folder. You open one in your browser like any saved web page. Nothing is uploaded anywhere; the
live demo is just those same files parked on a free host so people can see them without running
anything.

**Start with the three reports that need no custom tabs.** Financial Summary, Spending by
Category, and Spending by Tier run against the standard Tiller tabs you already have — the only
sheet change is adding two columns, `Bucket` and `Tier`, to your existing Categories tab, which is
just typing values into cells.

The other two — Downsize Model and Retirement Sustainability — need a custom tab each
(`Downsize Model`, `Portfolio Model`), which a stock Tiller sheet doesn't have. **You don't have to
add them to get started:** if a tab is missing, `run_demo.sh` skips that report with a note and
still builds the rest. Add them later, or never. (The advisor PDF draws on both models, so it's
built only once both tabs exist.)

`SETUP_GOOGLE_SHEETS.md` has the exact column/tab specs for all of it.

Once those exist: copy `scripts/config.py` to `scripts/config_local.py` (gitignored) and set
`HOUSEHOLD_NAME`, `SHEET_ID`, your real guaranteed-income category names, and your own
tax-rate assumptions — `render_*.py` never needs to change — then re-run `./run_demo.sh`.

## Security

Sharing a real Tiller sheet with a service account is low risk *if* the credential is handled
carefully — the sharing itself only grants access to that one spreadsheet, nothing else in your
Google account. The actual risk concentrates in the downloaded JSON key file, which is a
long-lived credential, not a login — anyone holding it can act as the service account until it's
revoked. This repo also connects by spreadsheet ID rather than searching Drive by name, so the
credential only ever requests the narrow `spreadsheets` scope, never `drive` — which is also why
the live demo's own sample-sheet sharing had to be set by hand rather than by the scripts here.

Full explanation, plus the two independent ways to revoke access if anything goes wrong, in
**"Understanding the security model"** in [`SETUP_GOOGLE_SHEETS.md`](SETUP_GOOGLE_SHEETS.md#understanding-the-security-model)
— read it before pointing this at anything real.

## What's not included

This is the reporting/modeling layer, not the full private toolkit it's drawn from — bank-import
scripts, one-off data-fixup scripts, and an operations runbook are specific to the original
household's setup and aren't part of this repo.

## Publishing your own copy

`docs/` is a committed, ready-to-serve copy of the demo output (the same six reports, plus the
sample PDF) — turn on **Settings → Pages → Deploy from branch → master /docs** on your fork and
it's live with no server. `run_demo.sh` writes to `reports/` (gitignored, scratch output); copy
whatever you want published into `docs/` the same way this repo's own copy was made.

## License

MIT — see [`LICENSE`](LICENSE).
