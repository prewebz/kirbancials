# Kirbancials

Personal finance dashboard for credit card statement summaries and other expenses. It is a [Streamlit](https://streamlit.io/) app meant to run as a free Python web service on [Render](https://render.com/).

After login the landing page is **Profit & Loss**. It totals cash income and expenses for a period you pick. Each expense name links to that source, and each income total links to that income source. **Daily spending** and **Monthly statements** stay available from the sidebar.

Expense sources are registered in `kirbancials/sources.py`. Citibank - 7690 reads transactions for `Citi AAdvantage card ending 7690`. Nordstrom reads statement months for `Nordstrom card ending 5488`. Any other Account in the data is still listed under its own name. See [Adding sources](#adding-sources).

Real statements, transactions, and income deposits never belong in git. The repository includes only fake round numbers under `sample_data/` for local development. Income tests use fake CSV text in the test file, not a data file.

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

The statement CSV upload on the Monthly statements page replaces that choice for the current session only. The file is not written to disk and is not committed.

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

### Income

Profit & Loss reads cash income from `FINANCE_INCOME_CSV`: the raw CSV text, not gzipped. If that variable is missing or blank, every income total is `$0.00` and the page says `No income data yet`. This file is not part of card spend or statement spend.

| Column | Role |
| --- | --- |
| Transaction ID | Stable id. A repeated id is kept once. A blank id is kept. |
| Date | `YYYY-MM-DD`. The deposit counts on this date. |
| Source | Income source id, such as `chipotle_nnn`. An id that is not registered still gets a row, labeled with the raw id. |
| Description | Shown on the source detail page. |
| Amount | Dollars received. |

Sources are registered in `kirbancials/sources.py` as `IncomeSource` (`id`, display name, frequency). Chipotle NNN is monthly. Waterleaf Apts (PEM Residential) is quarterly. Append another `IncomeSource` to add a row. The main page lists each source with its frequency beside the name, like a loan APR, and the total received in the selected period. That total links to `?source=<id>`, which lists the deposits in the period (date, description, amount) and a total.

Do not commit the CSV. On Render, set `FINANCE_INCOME_CSV` on the service **Environment** page the same way as the other CSV variables. Leave it unset until there is data.

## Profit & Loss

The period picker is at the top of the page. The choices are This month (the default), Last month, Quarter to date, Year to date, and Last 12 months. The exact dates are shown under the selector. This month, quarter to date, year to date, and the last 12 months end today. Last month is the previous calendar month, from the 1st through the last day. Quarter to date starts on the first day of the current calendar quarter. The last 12 months start the day after the same date a year ago.

For the selected period, four figures sit under the picker: total income, total expenses, net, and a monthly run rate. Net is green when positive and red when negative. The run rate is the average net per calendar month. A period shorter than a month is scaled to 30 days, and the card says so. Under that, a debt snapshot shows the total current credit card balance and estimated loan interest for this month (current balance and APR, labeled est.). Next to it, expected income for the next calendar month uses each source's frequency and last payment: monthly sources repeat the last amount, quarterly sources count only when the last payment plus three months falls in that next month, and irregular sources are excluded and named. A bar chart shows income and expenses for the last six months, with net as a line. The current month runs through today.

Card transactions only cover a short recent window. When the selected period or the chart starts before the earliest transaction, the page says `Expense data available from <date>` instead of treating the missing months as zero spend. Months before that date with no statement or loan expense are left off the expense bars.

The income and expense rows below the chart are unchanged.

Income is cash received on the deposit date. With `FINANCE_INCOME_CSV` unset or empty, the section shows `No income data yet` and total income `$0.00`. With a CSV, each registered source is one row: the name, the frequency beside it, and the total received in the period. A Source id that is not in the registry is still listed under that raw id. Total income is the sum of those rows. Income is not added into any expense total.

Expenses are one row per source: description, total spend, and data through. Total spend links to `?source=<id>` on the source detail page. The period is carried in the same query string. Logging in once covers every page in the session.

Transaction sources (such as Citibank - 7690) sum charges and subtract refunds. Pending charges are included. A negative row is a card payment, and is left out, when its category looks like `Loan Payments`, `Transfer In`, or `Transfer Out`, or when the merchant or description contains `PAYMENT`, `PYMT`, `AUTOPAY`, or `AUTO PAY`. A positive charge is still spend even if the text contains those words. That rule is `is_card_payment` in `kirbancials/spend.py`.

Statement sources (Nordstrom) sum `Net Spend` plus interest and fees for months whose statement closing date falls in the period. If the closing date is blank, the first day of `Month` is used so the row is not dropped. Quarter and year total rows are not added again.

Auto loans are listed after the cards. Their rows are estimated interest: current balance times APR divided by 365, times the number of days in the period. Loan payments in the transaction file are not spend. A missing balance shows `$0` and a note. The detail page shows the APR, balance, monthly and daily estimates, and the payment table.

Net profit / (loss) is total income minus total expenses. A loss is shown as `-$1,234.56`.

`python -m unittest discover` checks the payment rule, month boundaries, the sample expense totals, cash income, the run rate, expected next-month income, and period ranges.

Daily spending and Monthly statements are unchanged secondary pages.

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

- Another card: add one `ExpenseSource` in `kirbancials/sources.py` (`id`, display name, `transactions` or `statements`, and the Account values it owns), then put those Account values in the CSV. Accounts that are not listed still show up on Profit & Loss under their raw name.
- Other expenses: set `Source Kind` to `expense`. Statement-only columns can be left blank.
- Cash income: append one `IncomeSource` in `kirbancials/sources.py` (`id`, display name, and a frequency label such as `Monthly` or `Quarterly`). Use that id in the `Source` column of `FINANCE_INCOME_CSV`. Ids that are not listed still show under the raw id.
- Statement-file income: set `Source Kind` to `income` and put the amount in `Income` (or in `Net Spend` if that column is the amount). That stays on the statement summary. It does not change Profit & Loss cash income.
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
- **Income.** Set `FINANCE_INCOME_CSV` to the raw CSV (`Transaction ID`, `Date`, `Source`, `Description`, `Amount`). Plain text, not gzipped. Leave it unset to show income as `$0.00`.

Until those are set, the deployed app shows the fake samples and a banner. If the transaction sample file is missing as well, Daily spending says `No transaction data yet`.
