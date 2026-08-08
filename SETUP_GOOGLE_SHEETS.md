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
   `"Tiller Demo Foundation"` — change one or the other so they match).
2. Open the downloaded JSON key file and copy the `client_email` value (looks like
   `something@your-project.iam.gserviceaccount.com`).
3. In the Google Sheet, click **Share** and add that email address with **Editor** access.

A service account has no Google Drive storage of its own — it can't create a sheet for you, only
read/write one you've already created and shared with it. That's why this is a manual step rather
than something a script can do automatically.

## 3. Populate it

```
python3 scripts/generate_demo_data.py
```

This fabricates three years of synthetic data and writes it into the six tabs the report scripts
expect (Categories, Transactions, Balance History, Accounts, Downsize Model, Portfolio Model).

## 4. Using your own real data instead

Once you're comfortable with how it works, replace the synthetic rows on the **Transactions** and
**Categories** tabs with your own real Tiller export, and adjust **Balance History**, **Accounts**,
**Downsize Model**, and **Portfolio Model** to match your own numbers. Copy `scripts/config.py` to
`scripts/config_local.py` (gitignored) and set `HOUSEHOLD_NAME`, `SHEET_NAME`, and your own
guaranteed-income category names there — the report scripts themselves never need to change.
