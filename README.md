# Kirbancials

Personal finance dashboard for credit card statement summaries and other expenses. It is a [Streamlit](https://streamlit.io/) app meant to run as a free Python web service on [Render](https://render.com/).

The app has two tabs. **Daily spending** is the default: current balances, month-to-date charges, a daily spend table, and the transaction list. **Monthly statements** keeps the statement summary. The only card name shipped in code is the default used when a row has no `Account` value: `Nordstrom card ending 5488`. More cards are additional rows. See [Adding sources](#adding-sources).

Real statements and transactions never belong in git. The repository includes only fake round numbers under `sample_data/` for local development.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_PASSWORD='choose-a-password'
streamlit run app.py
```

Open the URL Streamlit prints and log in with that password. Log out from the sidebar.

If `APP_PASSWORD` is unset, the app shows an error and stops. It does not render balances, charts, or tables until the password matches. The comparison uses `hmac.compare_digest`.

With no data configured, the app loads the bundled samples and shows a banner that the numbers are fake.

## Data sources

### Statements

On each run the app picks the first source that exists:

1. `FINANCE_DATA_CSV` — the raw CSV text in an environment variable.
2. `/etc/secrets/statement_summary_combined.csv` — a Render secret file.
3. `FINANCE_DATA_PATH` — path to a CSV on the machine running the app. Useful locally. Render's disk is ephemeral, so this is not how production data should be stored.
4. `sample_data/example_statements.csv` — fake sample. A banner says so.

The statement CSV upload on the Monthly statements tab replaces that choice for the current session only. The file is not written to disk and is not committed.

`.gitignore` ignores `*.csv` except `sample_data/`, plus `*.xlsx`, `*.xls`, `*.gz`, `.streamlit/secrets.toml`, and `data/`.

### Card transactions

An outside job syncs Plaid transactions once a day. The app reads the first source that exists:

1. `FINANCE_TRANSACTIONS_CSV_GZ` — base64 of gzip-compressed CSV text. Use this when the plain CSV is too large for an environment variable.
2. `FINANCE_TRANSACTIONS_CSV` — the raw CSV text.
3. `/etc/secrets/transactions.csv` — a Render secret file named `transactions.csv`.
4. `sample_data/transactions.csv` — fake sample. A banner says so.

If none of those exist, the Daily spending tab says `No transaction data yet`.

Amount sign: positive is a charge, negative is a payment, refund, or credit. Rows are deduped on `Transaction ID`. When a pending row and a posted row share an ID, the posted row is kept.

| Column | Role |
| --- | --- |
| Transaction ID | Stable id from the sync. Used for dedupe. |
| Date | `YYYY-MM-DD`. |
| Account | Card name. Blank becomes `Nordstrom card ending 5488`. |
| Merchant | Merchant name. |
| Description | Stored, not shown in the table. |
| Amount | Positive charge or negative payment/credit. |
| Pending | `true` or `false`. |
| Category | Category label. |

### Current balances

1. `FINANCE_BALANCES_CSV` — raw CSV text.
2. `sample_data/balances.csv` — fake sample.

| Column | Role |
| --- | --- |
| Account | Card name. |
| As Of | ISO datetime. The latest value is shown as `Data last synced`. |
| Current Balance | Amount. |
| Available Credit | Amount. May be blank. |
| Credit Limit | Amount. May be blank. |

One row is kept per account, the row with the latest `As Of`.

## What the tabs show

**Daily spending**

- Balances, one row per account, with the as-of time
- Month-to-date spend per account (charges only, pending included and shown in its own column)
- Daily spend for the selected account in the current month, plus one bar chart of those days
- Transactions, newest first: date, account, merchant, amount, pending, category
- Filters: account, date range, and a checkbox to hide payments and credits

**Monthly statements**

- Latest statement balance, total net spend, total payments, interest + fees
- Statement balance by month (the only chart on this tab)
- Year, account, and source-kind filters
- Monthly detail
- Period totals, in an expander

Income rows are excluded from the spend totals. When any are present, an Income figure is added. Spend totals include credit card and expense rows.

Amounts are shown as stored. Negatives render as `-$1,234.56`.

### Statement CSV columns

| Column | Role |
| --- | --- |
| Year | Calendar year. Monthly rows are re-derived from `Month`. |
| Month | `YYYY-MM` for a statement month. Anything else is a period total, for example `Q4 2024 Total`, `2025 Total`, `2026 YTD Total`, or `Grand Total`. |
| Statement Closing Date | Optional date text. |
| Previous Balance | Amount. |
| New Charges (Purchases) | Amount. |
| Payments | Amount, kept with the sign stored in the file. |
| Credits/Returns | Amount. |
| Net Spend | Amount. |
| Fees | Amount. |
| Interest | Amount. |
| Statement Balance | Amount. |
| Credit Limit | Amount. |
| Available Credit | Amount. |
| Minimum Payment Due | Amount. |
| Payment Due Date | Optional date text. |
| Account | Optional. Blank or missing values become `Nordstrom card ending 5488`. |
| Source Kind | Optional. `credit_card` (default), `expense`, or `income`. |
| Income | Optional amount used when `Source Kind` is `income`. |

## Adding sources

- Another card: add statement rows, transaction rows, and a balance row with the same `Account` value. Leave `Source Kind` blank or set `credit_card`.
- Other expenses: set `Source Kind` to `expense`. Statement-only columns can be left blank.
- Income later: set `Source Kind` to `income` and put the amount in `Income` (or in `Net Spend` if that column is the amount). No dashboard rewrite is required.
- A new kind with its own total rules: add one `SourceKind` entry in `kirbancials/schema.py`.

Statement loading lives in `kirbancials/data.py`. Transactions and balances live in `kirbancials/activity.py`.

## Deploy on Render

`render.yaml` defines one web service:

- Name: `kirbancials`
- Runtime: Python
- Plan: `free` (spins down after about 15 minutes without traffic)
- Region: Oregon
- Build: `pip install -r requirements.txt`
- Start: Streamlit on `0.0.0.0:$PORT`, headless, usage stats off
- Health check: `/_stcore/health`
- `PYTHON_VERSION`: `3.11.9`
- `APP_PASSWORD`: `sync: false`, so the Blueprint prompts for it instead of storing it in the repo

Create the service from the Blueprint:

[https://dashboard.render.com/blueprint/new?repo=https://github.com/prewebz/kirbancials](https://dashboard.render.com/blueprint/new?repo=https://github.com/prewebz/kirbancials)

Apply it from the `main` branch. On first create, Render asks for `APP_PASSWORD`. That prompt does not run again on later Blueprint syncs; set or change the password on the service **Environment** page, then redeploy.

The service will not show the dashboard until `APP_PASSWORD` is set.

### Where to put the statement CSV

Do not commit it. Pick one of these after the service exists:

1. **Secret file (best fit for the existing export).** On the service **Environment** page, add a secret file named `statement_summary_combined.csv` and paste the CSV. Render mounts it at `/etc/secrets/statement_summary_combined.csv`. Secret files on a service share a 1 MB limit, which is enough for a statement summary. Save so Render redeploys.
2. **Environment variable.** Add `FINANCE_DATA_CSV` on the **Environment** page and paste the raw CSV as the value. This overrides the secret file when it is non-empty. Treat it as a secret. Very large files belong in the secret file instead.

`FINANCE_DATA_PATH` is not set in the Blueprint. Use it only for a local file.

### Where to put transactions and balances

Do not commit them. On the service **Environment** page:

- **Transactions.** Prefer a secret file named `transactions.csv` (mounted at `/etc/secrets/transactions.csv`). Or set `FINANCE_TRANSACTIONS_CSV` to the raw CSV. If the text is large, gzip it, base64-encode the bytes, and set `FINANCE_TRANSACTIONS_CSV_GZ`. That variable is checked first. Secret files on a service share a 1 MB limit.
- **Balances.** Set `FINANCE_BALANCES_CSV` to the raw CSV (`Account`, `As Of`, `Current Balance`, `Available Credit`, `Credit Limit`).

Until those are set, the deployed app shows the fake samples and a banner. If the transaction sample file is missing as well, Daily spending says `No transaction data yet`.
