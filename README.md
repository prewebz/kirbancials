# Kirbancials

Personal finance dashboard for credit card statement summaries and other expenses. It is a [Streamlit](https://streamlit.io/) app meant to run as a free Python web service on [Render](https://render.com/).

Phase 1 reads a statement CSV. The only card shipped in code is the default account name used when a row has no `Account` value: `Nordstrom card ending 5488`. More cards, expense sources, and later income are additional rows, not a rewrite. See [Adding sources](#adding-sources).

Real statements never belong in git. The repository includes only `sample_data/example_statements.csv`, which is fake round numbers for local development.

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

With no data configured, the app loads the bundled sample and shows a banner that it is sample data.

## Data sources

On each run the app picks the first source that exists:

1. `FINANCE_DATA_CSV` — the raw CSV text in an environment variable.
2. `/etc/secrets/statement_summary_combined.csv` — a Render secret file.
3. `FINANCE_DATA_PATH` — path to a CSV on the machine running the app. Useful locally. Render's disk is ephemeral, so this is not how production data should be stored.
4. `sample_data/example_statements.csv` — fake sample. A banner says so.

The sidebar CSV upload replaces that choice for the current session only. The file is not written to disk and is not committed.

`.gitignore` ignores `*.csv` except `sample_data/`, plus `*.xlsx`, `*.xls`, `.streamlit/secrets.toml`, and `data/`.

### CSV columns

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

Amounts are shown as stored. Negatives render as `-$3,820.06`.

The dashboard shows:

- Latest statement balance (sum of each card's newest statement in the filter)
- Total net spend
- Total payments
- Interest + fees
- Statement balance by month
- New charges vs payments
- Year, account, and source-kind filters
- Monthly detail
- Period totals, in an expander

Income rows are excluded from the spend totals. When any are present, an Income figure is added. Spend totals include credit card and expense rows.

## Adding sources

- Another card: add rows with a new `Account` value. Leave `Source Kind` blank or set `credit_card`. The account filter and the balance chart pick it up.
- Other expenses: set `Source Kind` to `expense`. Statement-only columns can be left blank.
- Income later: set `Source Kind` to `income` and put the amount in `Income` (or in `Net Spend` if that column is the amount). No dashboard rewrite is required.
- A new kind with its own total rules: add one `SourceKind` entry in `kirbancials/schema.py`.

The loader lives in `kirbancials/data.py`. `resolve_origin` is the ordered list of configured files. `load_dataset` normalizes whatever CSV it is given.

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

Until one of those is set, the deployed app logs in normally and then shows the fake sample banner.
