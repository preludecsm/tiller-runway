# Setting up the Google Sheet

Every report script reads its data from a Google Sheet via a **service account** — a
non-interactive Google identity your script authenticates as, rather than logging in as you.
This is the same pattern Tiller's own API access uses.

## 1. Create a Google Cloud service account

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project (or reuse
   one) and enable the **Google Sheets API** and **Google Drive API**.
2. Under *IAM & Admin → Service Accounts*, create a service account. Any name is fine.
3. Open it, go to *Keys → Add Key → Create new key → JSON*, and download the file.
4. Drop that JSON file in the root of this repo (it's gitignored — `*.json` is excluded, so it
   will never accidentally get committed). The scripts pick up whatever single `*.json` file they
   find there.

## 2. Create the sheet and share it with the service account

1. Create a new Google Sheet, named to match `SHEET_NAME` in `scripts/config.py` (defaults to
   `"Tiller Mockup"` — change one or the other so they match).
2. Open the downloaded JSON key file and copy the `client_email` value (looks like
   `something@your-project.iam.gserviceaccount.com`).
3. In the Google Sheet, click **Share** and add that email address with **Editor** access.

A service account has no Google Drive storage of its own — it can't create a sheet for you, only
read/write one you've already created and shared with it. That's why this is a manual step rather
than something a script can do automatically.

This Editor grant is separate from — and not the same as — any public link you might share the
sheet under. The service account needs Editor access so *your own scripts* can write to it; anyone
you share a public view link with should get **Viewer**, not Editor (see below).

## 3. Populate it

```
python3 scripts/generate_demo_data.py
```

This fabricates three years of synthetic data and writes it into the six tabs the report scripts
expect (Categories, Transactions, Balance History, Accounts, Downsize Model, Portfolio Model).

## 4. Using your own real data instead

**This does not work out of the box against a stock Tiller sheet.** Some of what these scripts
read is native to Tiller; some of it is custom structure the original household built by hand on
top of their own sheet, and you'd need to build the same thing on yours. Here's exactly which is
which.

### Native to Tiller — you likely already have these

- **Transactions**: `Date`, `Description`, `Category`, `Amount`, `Account` are standard Tiller
  columns. A `Tags` column is used too, if present, to reassign a tax payment to the calendar
  year it's actually for (tag a row `ty2025` etc.) — optional, everything works without it.
- **Categories**: the base columns (`Category`, `Group`, `Type`, ...) are standard. One thing to
  check: the scripts read the `Type` column ("Income"/"Expense") by its **position** — the 3rd
  column — not by header name, because that's where Tiller puts it by default. If yours differs,
  either move it or the scripts won't classify income vs. expense correctly.
- **Accounts** and **Balance History**: used for net worth and the mortgage principal/interest
  split. Balance History is a standard Tiller feature; exactly how your own Accounts tab flags
  "include this account in net worth" may not match what's read here (see the code comment in
  `generate_demo_data.py`'s `build_accounts()` — it's a positional convention from the original
  household's sheet, not something guaranteed to line up with yours. Worth checking against your
  actual Accounts tab before trusting the net worth figures).

### Custom — not part of Tiller, you have to build these

- **Two extra columns on Categories: `Bucket` and `Tier`.** Add them anywhere (found by header
  name, not position). `Bucket` is free text — a top-level grouping like "Housing" or "Travel"
  that categories roll up into on the Spending by Category page. `Tier` must contain the literal
  text `Tier 0`, `Tier 1`, `Tier 2`, or `Tier 3` (a regex matches on the word "Tier") — cut
  priority, 0 = never cut, 3 = purely elective. Leave a category's Tier blank and it defaults to
  Tier 1 with a warning printed to the console, rather than silently vanishing.

- **A whole new tab named exactly `Downsize Model`** — two columns, `Assumption` and `Value` (a
  third `Note` column is fine, ignored by the code). One row per assumption, label in column A,
  number in column B. Every row `render_downsize.py` actually reads:

  ```
  Current market value
  Original purchase price
  Capital improvements (basis add)
  Remaining mortgage balance
  SD documentary transfer tax rate
  Agent + closing costs rate
  Federal LTCG rate
  NIIT surtax
  CA capital gains rate
  Section 121 exclusion (MFJ)
  General inflation
  Rent inflation
  Expected real return (after-tax)
  Return volatility (annual, real)
  Monte Carlo trials
  Primary horizon (years)
  Federal marginal ordinary rate
  CA marginal ordinary rate
  Charitable giving, deductible (annual)
  Other itemized deductions excl. home (state tax + personal property)
  Federal standard deduction (MFJ)
  Federal SALT cap
  ```

- **A whole new tab named exactly `Portfolio Model`** — same two-column label/value format:

  ```
  Expected real return (after-tax)
  Return volatility (annual, real)
  Monte Carlo trials
  Primary horizon (years)
  General inflation
  Lifestyle spending real growth
  Guaranteed income real growth
  ```

  Labels must match exactly (case and punctuation) — the code does an exact-string lookup, not a
  fuzzy match. See `scripts/generate_demo_data.py` for a working example of every row above with
  a value in it, if you want something to copy and edit rather than build from this list.

### Putting it together

1. `render_dashboard.py` and `render_spending.py` will run against a fairly stock Tiller sheet —
   the Spending by Category breakdown wants the `Bucket` column to be meaningful, but nothing
   will crash without it.
2. `render_tiers.py` needs the `Tier` column added.
3. `render_downsize.py` and `render_sustainability.py` need their entire model tabs built from
   scratch — there's no Tiller feature to migrate from, no shortcut.

Once the custom pieces exist, replace the synthetic rows on **Transactions** and **Categories**
with your own real Tiller export, and adjust **Balance History**, **Accounts**, **Downsize
Model**, and **Portfolio Model** to match your own numbers. Copy `scripts/config.py` to
`scripts/config_local.py` (gitignored) and set `HOUSEHOLD_NAME`, `SHEET_NAME`, and your own
guaranteed-income category names there — the report scripts themselves never need to change.

## About the published demo sheet

If you're looking at the maintainer's own published copy of the demo sheet (linked from the
README) rather than setting up your own: it's shared **view-only**. You can't edit it, and it
isn't kept in sync with anything — it's a one-time snapshot, not a live feed, so there's no
"refresh" to wait for or break. To use it, **File → Make a copy** — that works on a view-only
sheet and gives you your own private, editable copy to build from, following step 4 above.
