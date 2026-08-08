# Setting up the Google Sheet

Every report script reads its data from a Google Sheet via a **service account** — a
non-interactive Google identity your script authenticates as, rather than logging in as you.
This is the same pattern Tiller's own API access uses.

**Read "Understanding the security model" below before you start** if you're putting real
financial data anywhere near this — it explains exactly what each step does and doesn't expose,
and it's short.

## 1. Create a Google Cloud service account

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project (or reuse
   one) and enable the **Google Sheets API**. (Not the Drive API — this project deliberately
   doesn't use it; see below.)
2. Under *IAM & Admin → Service Accounts*, create a service account. Any name is fine.
3. Open it, go to *Keys → Add Key → Create new key → JSON*, and download the file.

   **Risk concentrates here.** This file is a standing credential, not a login — whoever holds it
   can act as this service account indefinitely, with no password or 2FA prompt, until you
   revoke it. Treat it like a password: never email it, never paste it into a chat, never commit
   it to git.
4. Drop that JSON file in the root of this repo. It's gitignored (`*.json` is excluded) so it
   won't get committed by accident — but double-check with `git status` after adding it the first
   time, especially if you've changed the `.gitignore`. The scripts pick up whatever single
   `*.json` file they find in the repo root.

## 2. Create the sheet and share it with the service account

1. Create a new Google Sheet. Open it and copy the **spreadsheet ID** out of the URL — the long
   string between `/d/` and `/edit`, e.g. `docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit`.
2. Set `SHEET_ID` in `scripts/config.py` (or, better, in `scripts/config_local.py` — see step 4)
   to that value.
3. Open the downloaded JSON key file and copy the `client_email` value (looks like
   `something@your-project.iam.gserviceaccount.com`).
4. In the Google Sheet, click **Share** and add that email address with **Editor** access.

   **What this grants, precisely:** access to *this one spreadsheet*, and nothing else — not your
   Gmail, not your other Drive files, not your Google account generally. Google's sharing model
   is per-file. Revoking it later is one click (remove the service account from this sheet's
   share list) and takes effect immediately, independent of whether you also rotate the key.

A service account has no Google Drive storage of its own — it can't create a sheet for you, only
read/write one you've already created and shared with it. That's why this is a manual step rather
than something a script can do automatically.

The Editor grant above is separate from — and not the same as — any public link you might share
the sheet under later. The service account needs Editor access so *your own scripts* can write to
it; anyone you share a public view link with should get **Viewer**, not Editor (see below).

## 3. Populate it

```
python3 scripts/generate_demo_data.py
```

This fabricates three years of synthetic data and writes it into the six tabs the report scripts
expect (Categories, Transactions, Balance History, Accounts, Downsize Model, Portfolio Model).

## 4. Using your own real data instead

**This is the point where real financial data enters the picture — read "Understanding the
security model" below first if you haven't.** Everything above this point can be done with zero
risk beyond the usual "don't leak your API key" caution, since it's all synthetic. Once you paste
in a real Tiller export, the sheet you shared in step 2 actually matters.

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
`scripts/config_local.py` (gitignored) and set `HOUSEHOLD_NAME`, `SHEET_ID`, and your own
guaranteed-income category names there — the report scripts themselves never need to change.

## Understanding the security model

Worth reading in full before you point this at anything real. Two separate things are going on,
and they carry very different levels of risk.

**Sharing the sheet with the service account (step 2) is low risk.** It's the same mechanism as
sharing a doc with a specific person's email — the service account gets access to *that one
spreadsheet*, nothing else. It can't see your Gmail, your other Drive files, or anything else in
your Google account. This repo also connects by the sheet's ID (`open_by_key`), not by searching
for it by name, so the credential only requests the narrow `spreadsheets` OAuth scope — it never
asks for `drive` scope at all, which is what would be needed to search your Drive by filename. The
practical effect: even if something went wrong, there's no code path here that could reach any
file you didn't explicitly share.

**The downloaded JSON key file (step 1) is where real risk concentrates.** It's a long-lived
credential, not a session — anyone holding it can authenticate as the service account and act on
whatever's shared with it, indefinitely, with no login prompt, until the key is revoked. The most
common way these leak in practice is someone accidentally committing the key file to a public
GitHub repo. This project's `.gitignore` excludes `*.json` specifically to prevent that, but
`.gitignore` only helps if you don't override it — don't `git add -f` this file, and if you ever
restructure `.gitignore`, check the key stays excluded.

**Two independent kill-switches, either one stops all access:**
1. Remove the service account from the sheet's share list (Google Sheets → Share) — instant,
   no code change, works even if the key is still valid.
2. Delete or rotate the key in Google Cloud Console (*IAM & Admin → Service Accounts → Keys*) —
   kills the credential entirely, even if the sheet sharing is untouched.

**If you're evaluating whether to point this at your real financial data at all:** this is the
same authentication pattern most third-party financial tools use to connect to a Google Sheet or
similar data source — a scoped, revocable, per-resource credential, not your actual Google
password and not a broad "read everything" OAuth consent. That's meaningfully safer than the
alternatives, but "meaningfully safer" isn't "risk-free" — the key-handling discipline above is
what actually keeps it safe, not the pattern by itself.

## About the published demo sheet

If you're looking at the maintainer's own published copy of the demo sheet (linked from the
README) rather than setting up your own: it's shared **view-only**. You can't edit it, and it
isn't kept in sync with anything — it's a one-time snapshot, not a live feed, so there's no
"refresh" to wait for or break. To use it, **File → Make a copy** — that works on a view-only
sheet and gives you your own private, editable copy to build from, following step 4 above.
