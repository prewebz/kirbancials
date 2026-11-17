# Kirbancials

Personal finance dashboard for credit card statement summaries and other expenses. It is a [Streamlit](https://streamlit.io/) app meant to run as a free Python web service on [Render](https://render.com/).

After login the landing page is **Profit & Loss**. **Balance Sheet** is the next page in the sidebar. **VWH P&L** follows Balance Sheet. Profit & Loss totals cash income and expenses for a period you pick. Each expense name links to that source, and each income total links to that income source. **Daily spending** and **Monthly statements** stay available from the sidebar.

Expense sources are registered in `kirbancials/sources.py`. Citibank - 7690 reads transactions for `Citi AAdvantage card ending 7690`. Nordstrom reads statement months for `Nordstrom card ending 5488`. Any other Account in the data is still listed under its own name. See [Adding sources](#adding-sources).

Real statements, transactions, and income deposits never belong in git. The repository includes only fake round numbers under `sample_data/` for local development. Income tests use fake CSV text in the test file, not a data file.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_PASSWORD='choose-a-password'
export AUTH_COOKIE_SECRET='choose-a-long-random-secret'
streamlit run app.py
```

Open the URL Streamlit prints and log in with that password. **Refresh data** is at the top of the sidebar, and a smaller **Refresh** is in the page header. Both clear Streamlit's `st.cache_data` and `st.cache_resource` caches, read the env vars and files again, and rerun. You stay logged in. The `Last refreshed:` caption uses America/Phoenix.

A browser reload keeps the login for 30 days. The `kirbancials_auth` cookie is an HMAC-SHA256 token, not the password. The signing key comes from `APP_PASSWORD` and `AUTH_COOKIE_SECRET`, and the signed payload includes the expiry time. On load the app reads `st.context.cookies` and skips the password when the token is valid. Changing either value makes old cookies fail. **Log out** clears the cookie.

The cookie is `SameSite=Lax`. It is `Secure` on HTTPS, which is how Render serves the app. Local HTTP omits `Secure` so the browser will store the cookie. It is not `HttpOnly`: the password check runs in the Streamlit session, and a page script cannot set `HttpOnly`. If `AUTH_COOKIE_SECRET` is unset, login still lasts until the tab session ends, and the sidebar says remembered login is off.

If `APP_PASSWORD` is unset, the app shows an error and stops. It does not render balances, charts, or tables until the password matches. The comparison uses `hmac.compare_digest`. There is no separate Balance Sheet password.

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

`Account Type` is optional. The file does not have it unless you add the column. Without it, an account registered as an auto loan is a loan, and every other account is a credit card. There is no way to tell a checking or savings account from a card. The Balance Sheet uses these values when the column is present:

| Account Type | Balance sheet group |
| --- | --- |
| `depository`, `cash`, `checking`, or `savings` | Cash |
| `loan`, `mortgage`, `auto`, or `auto loan` | Other loans |
| `credit`, `credit card`, `credit_card`, `card`, or blank | Credit cards, unless the account is a registered auto loan |

A recognized value wins over the auto-loan name list. See [Balance Sheet](#balance-sheet).

### Income

Profit & Loss reads cash income from `FINANCE_INCOME_CSV`: the raw CSV text, not gzipped. If that variable is missing or blank, every income total is `$0.00` and the page says `No income data yet`. This file is not part of card spend or statement spend.

| Column | Role |
| --- | --- |
| Transaction ID | Stable id. A repeated id is kept once. A blank id is kept. |
| Date | `YYYY-MM-DD`. The deposit counts on this date. |
| Source | Income source id, such as `chipotle_nnn`. An id that is not registered still gets a row, labeled with the raw id. |
| Description | Shown on the source detail page. |
| Amount | Dollars received. |

Sources are registered in `kirbancials/sources.py` as `IncomeSource` (`id`, display name, frequency). Chipotle NNN is monthly. Nexus Academy - FFB loan cover is monthly. Waterleaf Apts (PEM Residential) is quarterly. MF3Distro, Nexus Academy - Profit distributions, and Nexus Academy - Working capital loan repayment are irregular. Append another `IncomeSource` to add a row. The registry does not store an expected dollar amount. The main page lists each source with its frequency beside the name, like a loan APR, and the total received in the selected period. That total links to `?source=<id>`, which lists the deposits in the period (date, description, amount) and a total.

Do not commit the CSV. On Render, set `FINANCE_INCOME_CSV` on the service **Environment** page the same way as the other CSV variables. Leave it unset until there is data.

### Loan schedules

Profit & Loss reads fixed amortization schedules from `FINANCE_LOAN_SCHEDULE_CSV`: the raw CSV text, not gzipped. If that variable is missing or blank, scheduled loans are left out and the page matches a run with no schedule. Do not commit the CSV. On Render, set it on the service **Environment** page the same way as the other CSV variables.

The header row is exactly:

`Loan ID,Lender,Loan Name,Collateral,Rate,Payment Number,Payment Date,Payment,Interest,Principal,Remaining Balance`

| Column | Role |
| --- | --- |
| Loan ID | Groups rows into one expense. |
| Lender | First half of the expense name. |
| Loan Name | Second half of the expense name. |
| Collateral | Shown on the source detail page. |
| Rate | Annual percent, such as `5.9`. The row is labeled `Lender - Loan Name (5.9% sched.)`. |
| Payment Number | One row per scheduled payment. `0` may be prepaid interest at closing and counts like any other row on that date. |
| Payment Date | `YYYY-MM-DD`. Interest counts when this date falls in the selected period. |
| Payment | Scheduled payment amount. Not an expense. Shown on the detail page and as the next payment on the debt snapshot. |
| Interest | The expense for the period. Principal is not included. |
| Principal | Not an expense. Shown on the detail page. |
| Remaining Balance | Balance after that payment. |

Each Loan ID is one expense row, for example `First Fidelity Bank - Chipotle cash-out loan (5.9% sched.)`. The period amount is the sum of Interest for payment dates in that period. That total is part of total expenses, net, the monthly run rate, and the six-month chart, for every period choice. The detail page lists the schedule rows in the period: date, payment, interest, principal, and remaining balance.

The debt snapshot keeps the credit-card total. Its loan-interest figure adds this month's scheduled interest to the existing balance-and-APR estimate. Each scheduled loan is listed there with its remaining balance and its next payment date and amount. The remaining balance is the Remaining Balance of the latest payment dated on or before today. When today is before every payment, it is that first row's balance before the payment (remaining balance plus principal).

A CSV that cannot be read shows a warning and leaves scheduled loans out. The rest of the page still loads. The warning does not include cells from the file.

### Assets

The Balance Sheet reads manually entered assets from `FINANCE_ASSETS_CSV`: the raw CSV text, not gzipped. If that variable is missing or blank, the page still loads. It shows cash and liabilities from the other files, and says `No assets are configured.` Do not commit the CSV. On Render, set it on the service **Environment** page the same way as the other CSV variables.

The header row is exactly:

`Asset ID,Asset Name,Category,Acquired Date,Cost Basis,Market Value,Valuation Date,Valuation Source,Linked Loan ID,Income Source ID,Notes`

| Column | Role |
| --- | --- |
| Asset ID | Stable id for the row. A repeated id makes the file unreadable. |
| Asset Name | Shown on the card. |
| Category | Group on the assets table. Examples: `Real Estate`, `Note Receivable`, `Investment`, `Other`. |
| Acquired Date | `YYYY-MM-DD`. May be blank. |
| Cost Basis | Plain decimal, such as `1000.00` or `1000`. May be blank. No dollar sign and no thousands separator. |
| Market Value | Plain decimal. May be blank. A blank value is left out of the totals and shown as `n/a`. |
| Valuation Date | `YYYY-MM-DD`. May be blank. |
| Valuation Source | Where the value came from, such as an appraisal. May be blank. |
| Linked Loan ID | `Loan ID` from `FINANCE_LOAN_SCHEDULE_CSV`. Blank when nothing is linked. |
| Income Source ID | Income source id, such as `chipotle_nnn`. Blank when the asset has no income row. |
| Notes | Free text. May be blank. |

A CSV that cannot be read shows a warning and leaves the assets off the page. The warning does not include cells from the file. Cash and liabilities still show.

### Balance Sheet

The page is the second sidebar entry, after Profit & Loss. Logging in once covers it. The line under the title is `As of` today's date.

Three cards sit under that line: total assets, total liabilities, and net worth. Net worth is total assets minus total liabilities. It is green when positive and red when negative.

Total assets are cash plus each asset's market value. Total liabilities are the loan schedule's current remaining balances, plus other loans, plus credit cards.

Under the cards, one bordered card per asset shows the market value with its valuation date and source, the cost basis, and the gain or loss versus cost. The linked loan is the schedule's current remaining balance for that Loan ID. Equity is the market value minus that balance. With no linked loan, equity is the market value and the loan is `n/a`. LTV is the loan divided by the market value. When an income source id is set, the card also shows trailing-12-month cash income for that source, the yield (that income divided by market value), and cash on equity (that income minus the linked loan's trailing-12-month scheduled payments, divided by equity). A missing input is `n/a`.

The assets table lists cash, then each asset, once. There is no category subtotal. Total assets is a line under the table. Cash is the current balance of accounts labeled `depository`, `cash`, `checking`, or `savings`. The liabilities table lists mortgages and other property loans from the schedule, other loans (auto loans and balance rows typed as loans), and credit cards. A group with two or more rows has a subtotal under the table. A group with one row does not. Dollar, percent, and date columns stay numbers, so clicking a header sorts by value. The cells still show `$` with commas, percents, and dates. A balance row whose account is a scheduled loan's Loan ID, or that loan's `Lender - Loan Name`, is not listed again. The schedule's remaining balance is the one that counts. The match ignores capitals.

The bottom of the page says `Market values are entered manually from appraisals; update when a new valuation is available.`

## Profit & Loss

The period picker is at the top of the page. The choices are This month (the default), Last month, Quarter to date, Year to date, and Last 12 months. The exact dates are shown under the selector. This month, quarter to date, year to date, and the last 12 months end today. Last month is the previous calendar month, from the 1st through the last day. Quarter to date starts on the first day of the current calendar quarter. The last 12 months start the day after the same date a year ago.

For the selected period, four figures sit under the picker: total income, total expenses, net, and a monthly run rate. Net is green when positive and red when negative. The run rate is the average net per calendar month. A period shorter than a month is scaled to 30 days, and the card says so. Under that, a debt snapshot shows the total current credit card balance and estimated loan interest for this month (current balance and APR, labeled est.). When a loan schedule is configured, that interest figure also includes scheduled interest for payment dates this month, and each scheduled loan is listed with its remaining balance and next payment. Next to it, expected income for the next calendar month uses each source's frequency and last payment: monthly sources repeat the last amount, quarterly sources count only when the last payment plus three months falls in that next month, and irregular sources are excluded and named. A bar chart shows income and expenses for the last six months, with net as a line. The current month runs through today.

Card transactions only cover a short recent window. When the selected period or the chart starts before the earliest transaction, the page says `Expense data available from <date>` instead of treating the missing months as zero spend. Months before that date with no statement or loan expense are left off the expense bars.

The income and expense rows below the chart are unchanged.

Income is cash received on the deposit date. With `FINANCE_INCOME_CSV` unset or empty, the section shows `No income data yet` and total income `$0.00`. With a CSV, each registered source is one row: the name, the frequency beside it, and the total received in the period. A Source id that is not in the registry is still listed under that raw id. Total income is the sum of those rows. Income is not added into any expense total.

Expenses are one row per source: description, total spend, and data through. Total spend links to `?source=<id>` on the source detail page. The period is carried in the same query string. Logging in once covers every page in the session.

Transaction sources (such as Citibank - 7690) sum charges and subtract refunds. Pending charges are included. A negative row is a card payment, and is left out, when its category looks like `Loan Payments`, `Transfer In`, or `Transfer Out`, or when the merchant or description contains `PAYMENT`, `PYMT`, `AUTOPAY`, or `AUTO PAY`. A positive charge is still spend even if the text contains those words. That rule is `is_card_payment` in `kirbancials/spend.py`.

Statement sources (Nordstrom) sum `Net Spend` plus interest and fees for months whose statement closing date falls in the period. If the closing date is blank, the first day of `Month` is used so the row is not dropped. Quarter and year total rows are not added again.

Auto loans are listed after the cards. Their rows are estimated interest: current balance times APR divided by 365, times the number of days in the period. Loan payments in the transaction file are not spend. A missing balance shows `$0` and a note. The detail page shows the APR, balance, monthly and daily estimates, and the payment table.

Scheduled loans from `FINANCE_LOAN_SCHEDULE_CSV` are listed with that loan interest. The amount is scheduled Interest for payment dates in the period, including a payment number 0 row. Principal is not an expense. The detail page shows the rate, remaining balance, next payment, and the schedule rows in the period. With the variable unset or empty, those rows are absent.

Net profit / (loss) is total income minus total expenses. A loss is shown as `-$1,234.56`.

`python -m unittest discover` checks the payment rule, month boundaries, the sample expense totals, cash income, the run rate, expected next-month income, loan schedules, period ranges, and balance sheet totals.

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

### VWH P&L

VWH P&L is the sidebar page after Balance Sheet. It is the monthly profit and loss for the VWH houses in Indianapolis, managed by Kline Property Management.

The page reads `FINANCE_VWH_PL_CSV`: the raw CSV text, not gzipped. If that variable is missing or blank, the page says `No VWH P&L data yet` and the other pages still load. Do not commit the CSV. On Render, set it on the service **Environment** page the same way as the other CSV variables.

The header row is exactly:

`Month,Property ID,Address,Section,Account Number,Account Name,Amount,Source`

| Column | Role |
| --- | --- |
| Month | `YYYY-MM`. The page opens on the latest month. The month list is newest first. |
| Property ID | House key, such as `VWH720`. |
| Address | Name in the house table. |
| Section | `Income`, `Expense`, or `Below the line`. |
| Account Number | Account key. A missing account for a house is zero. |
| Account Name | Line on the statement. |
| Amount | Plain decimal, such as `1000.00`, `1000`, or `-50.00`. No dollar sign and no thousands separator. A negative income amount is a refund and reduces income. |
| Source | `Kline P&L` or `Bank`. |

Net income is income minus expense. Below-the-line rows are left out of that net. Account `BANK-TAX-PEN`, named `Tax Penalties (excluded)`, is the below-the-line row. It is shown as a memo line when the amount is not zero. A house with no income still appears when it has any row in the month.

Under the month list, the totals choice is This month (the default), Year to date, or Trailing 12 months. The range ends on the month you picked. Three cards show income, expense, and net income. Expense is listed by account under the cards. The label starts with the account number, such as `5000 Property Management Expenses`, so account 5000 and account 6400 stay apart even though both are named Property Management Expenses. The house page uses the same labels. The house table shows address, income, expense, and net. Choose an address to open that house: its income lines, its expense lines, net income, and the memo line when there is one. Back to portfolio returns to the table. Net is green when positive and red when negative. A loss is shown as `-$1,234.56`.

Below the house table, Trending uses the latest months in the file. Property taxes read `FINANCE_VWH_TAX_CSV`: the raw CSV text, not gzipped. One row is one pay year and one house, joined to the profit and loss on Property ID. Indianapolis bills the tax in two installments, spring due May 10 and fall due November 10. The amounts are the tax only. Penalties are already left out. If that variable is missing or blank, Trending still shows the run rate and says `Property tax data isn't loaded yet`. Do not commit the CSV.

The header row is exactly:

`Pay Year,Property ID,Address,Parcel,Spring Tax,Fall Tax,Annual Tax,Spring Status,Fall Status`

The tax table lists each house for the pay year: address, spring, fall, annual, the status of each installment, and the change from the prior pay year in dollars and percent. The newest pay year opens first. A pay year list appears when the file has more than one year. The last row is the portfolio total. Change is blank when that house has no row for the prior year.

Estimated annual run rate is one row per house, plus a total: income, operating expense, property tax, insurance, net, and net per month. Income is every Kline P&L row in the Income section. Operating expense is every Kline P&L row in the Expense section, whichever account number Kline used. Each of those two is the average month over the latest 12 months in the file, or fewer when the file has fewer months, times 12. A month that is in the file but has no row for that house counts as zero. The note under the table names the months. Property tax is that house's annual tax for the latest pay year. When the house has no tax bill, property tax is the sum of account `BANK-TAX` over those same months. Insurance is account `BANK-INS` in the latest of those months, times 12. Prior-year tax (`BANK-TAX-PY`) and penalties (`BANK-TAX-PEN`) are left out of the run rate. Net is income minus operating expense, property tax, and insurance.

The monthly chart shows income, expense, and net for each month in the file. Expense matches the rest of the page. Choose one house to chart that house. On a house page, Trending shows that house's tax row and run rate.

A property-tax CSV that cannot be read shows a warning. The run rate still shows, using tax paid in the profit and loss. The warning does not include cells from the file.

A profit-and-loss CSV that cannot be read shows a warning and no figures. The warning does not include cells from the file.

## Adding sources

- Another card: add one `ExpenseSource` in `kirbancials/sources.py` (`id`, display name, `transactions` or `statements`, and the Account values it owns), then put those Account values in the CSV. Accounts that are not listed still show up on Profit & Loss under their raw name.
- Other expenses: set `Source Kind` to `expense`. Statement-only columns can be left blank.
- Cash income: append one `IncomeSource` in `kirbancials/sources.py` (`id`, display name, and a frequency label such as `Monthly` or `Quarterly`). Use that id in the `Source` column of `FINANCE_INCOME_CSV`. Ids that are not listed still show under the raw id. The next-month estimate uses that frequency and the last deposit. It does not read a stored expected amount.
- A fixed amortization loan: put one row per payment in `FINANCE_LOAN_SCHEDULE_CSV`. Each Loan ID becomes an expense. Do not add it to `EXPENSE_SOURCES`, and do not commit the schedule.
- Statement-file income: set `Source Kind` to `income` and put the amount in `Income` (or in `Net Spend` if that column is the amount). That stays on the statement summary. It does not change Profit & Loss cash income.
- A new kind with its own total rules: add one `SourceKind` entry in `kirbancials/schema.py`.

Statement loading lives in `kirbancials/data.py`. Transactions and balances live in `kirbancials/activity.py`. Asset rows live in `kirbancials/assets.py`. The balance sheet lives in `kirbancials/balance_sheet.py`. The VWH portfolio lives in `kirbancials/vwh.py`. Trending math lives in `kirbancials/vwh_trend.py`.

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
- `AUTH_COOKIE_SECRET`: `sync: false`. A long random string that signs the remembered-login cookie. Set it on the service **Environment** page. Changing it signs everyone out of remembered sessions.

Create the service from the Blueprint:

[https://dashboard.render.com/blueprint/new?repo=https://github.com/prewebz/kirbancials](https://dashboard.render.com/blueprint/new?repo=https://github.com/prewebz/kirbancials)

Apply it from the `main` branch. On first create, Render asks for `APP_PASSWORD` and `AUTH_COOKIE_SECRET`. That prompt does not run again on later Blueprint syncs; set or change them on the service **Environment** page, then redeploy.

The service will not show the dashboard until `APP_PASSWORD` is set.

### Where to put the statement CSV

Do not commit it. Pick one of these after the service exists:

1. **Secret file (best fit for the existing export).** On the service **Environment** page, add a secret file named `statement_summary_combined.csv` and paste the CSV. Render mounts it at `/etc/secrets/statement_summary_combined.csv`. Secret files on a service share a 1 MB limit, which is enough for a statement summary. Save so Render redeploys.
2. **Environment variable.** Add `FINANCE_DATA_CSV` on the **Environment** page and paste the raw CSV as the value. This overrides the secret file when it is non-empty. Treat it as a secret. Very large files belong in the secret file instead.

`FINANCE_DATA_PATH` is not set in the Blueprint. Use it only for a local file.

### Where to put transactions and balances

Do not commit them. On the service **Environment** page:

- **Transactions.** Prefer a secret file named `transactions.csv` (mounted at `/etc/secrets/transactions.csv`). Or set `FINANCE_TRANSACTIONS_CSV` to the raw CSV. If the text is large, gzip it, base64-encode the bytes, and set `FINANCE_TRANSACTIONS_CSV_GZ`. That variable is checked first. Secret files on a service share a 1 MB limit.
- **Balances.** Set `FINANCE_BALANCES_CSV` to the raw CSV (`Account`, `As Of`, `Current Balance`, `Available Credit`, `Credit Limit`). To show checking and savings on the Balance Sheet, add an `Account Type` column. Use `depository`, `cash`, `checking`, or `savings` for cash. Use `loan` for a loan that is not already a registered auto loan. Use `credit` for a card. Leave the column off and every unlabeled account that is not a registered auto loan stays a credit card.
- **Income.** Set `FINANCE_INCOME_CSV` to the raw CSV (`Transaction ID`, `Date`, `Source`, `Description`, `Amount`). Plain text, not gzipped. Leave it unset to show income as `$0.00`.
- **Loan schedule.** Set `FINANCE_LOAN_SCHEDULE_CSV` to the raw CSV. The header is `Loan ID,Lender,Loan Name,Collateral,Rate,Payment Number,Payment Date,Payment,Interest,Principal,Remaining Balance`. Plain text, not gzipped. Leave it unset to leave scheduled loans out.
- **Assets.** Set `FINANCE_ASSETS_CSV` to the raw CSV. The header is `Asset ID,Asset Name,Category,Acquired Date,Cost Basis,Market Value,Valuation Date,Valuation Source,Linked Loan ID,Income Source ID,Notes`. Plain text, not gzipped. Leave it unset to show cash and liabilities only.
- **VWH P&L.** Set `FINANCE_VWH_PL_CSV` to the raw CSV. The header is `Month,Property ID,Address,Section,Account Number,Account Name,Amount,Source`. Plain text, not gzipped. Leave it unset and the page says `No VWH P&L data yet`.
- **VWH property tax.** Set `FINANCE_VWH_TAX_CSV` to the raw CSV. The header is `Pay Year,Property ID,Address,Parcel,Spring Tax,Fall Tax,Annual Tax,Spring Status,Fall Status`. Plain text, not gzipped. Leave it unset and Trending still shows the run rate, and says property tax data isn't loaded yet.

Until those are set, the deployed app shows the fake samples and a banner. If the transaction sample file is missing as well, Daily spending says `No transaction data yet`. VWH P&L says `No VWH P&L data yet` until `FINANCE_VWH_PL_CSV` is set.
