# Running LeadFinder in the cloud (free)

When this is set up, GitHub runs LeadFinder every 3 days. Each run does about 60 new property searches (it works through all 45 areas in turn) and re-checks older "New" leads. Everything lands in a Google Sheet that you edit from your phone or PC. The local menu (`run.bat`) uses the same Sheet.

Setup takes about 30–45 minutes, once.

---

## Part A: Google Sheet and a "robot" account (15 min)

The scheduled job needs its own Google login to write to your Sheet. This login is called a *service account*, and it's free.

1. Go to **console.cloud.google.com** and select the **same project** that holds your Places API key.
2. Go to **APIs & Services → Library**, search for **Google Sheets API**, and click **Enable**.
3. Go to **IAM & Admin → Service Accounts → + Create service account**.
   * Name: `leadfinder`. Click **Create and continue**, skip the roles step, then click **Done**.
4. Click the new service account, open the **Keys** tab, then **Add key → Create new key → JSON → Create**. A `.json` file downloads.
   * Rename it to **`service-account.json`** and move it into the `leadfinder` folder.
   * Treat it like a password. It is already excluded from GitHub uploads.
5. Copy the service account's email address. It looks like `leadfinder@your-project.iam.gserviceaccount.com`.  leadfinder@wordpress-514309.iam.gserviceaccount.com
6. Open **sheets.new** to create a blank Google Sheet and name it something like *dotcreative Leads*.
   * Click **Share**, paste the service account email, set it to **Editor**, untick "Notify", and click **Share**.
7. Copy the Sheet ID from the address bar. It's the long part between `/d/` and `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit`
8. Open `config.json` and paste the ID into `"google_sheet_id": ""`. Save the file.
9. Double-click **run.bat**. The header should now say **Leads kept in: Google Sheet**.
   * Choose **9** to copy your current leads from `leads.xlsx` into the Sheet, one time only.
   * Choose **7** to open the Sheet. It already has the colours, the Status dropdown, and frozen columns.

From now on, the menu writes to the Sheet. `leads.xlsx` stays as an old backup.

---

## Part B: GitHub (15 min)

1. Create a free account at **github.com** if you don't have one.
2. Click **+ → New repository**. Name it `leadfinder`, choose **Private**, and **don't** add a README. Click **Create**.
3. Open a terminal in the `leadfinder` folder and run these one at a time, replacing `YOUR-USERNAME` with your GitHub username:

```bash
git add -A
```

```bash
git commit -m "LeadFinder"
```

```bash
git remote add origin https://github.com/YOUR-USERNAME/leadfinder.git
```

```bash
git push -u origin main
```

   A browser window will ask you to sign in to GitHub the first time.
   Your keys, `config.json`, `service-account.json` and the Excel file are **not** uploaded; `.gitignore` excludes them.

4. On GitHub, open the repository, then **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name                            | Value                                                                             |
| ------------------------------- | --------------------------------------------------------------------------------- |
| `GOOGLE_PLACES_API_KEY`       | the key from your `config.json`                                                 |
| `LEADS_SHEET_ID`              | the Sheet ID from step A7                                                         |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | open `service-account.json` in Notepad, copy **everything**, and paste it |
| `OPEN_PAGERANK_API_KEY`       | optional, if you get the free key                                                 |

5. Optional, on the **Variables** tab next to Secrets:

   * `AUTO_INTERVAL_DAYS`: `3` is the default. Use `1` for daily.
   * `AUTO_CALLS_PER_RUN`: `60` is the default. This sets the number of Google searches per run.
6. Test it: go to **Actions → LeadFinder auto run → Run workflow**. After 5–10 minutes, open the run to see a summary with the new Hot leads, then check your Sheet.

---

## Staying at $0

| Thing                         | Free allowance                                                      | What this setup uses                                                                                                                                              |
| ----------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GitHub Actions (private repo) | 2,000 minutes/month                                                 | About 10 runs × ~8 min, plus daily 30-second "not today" checks: about 100 min                                                                                   |
| Google Places                 | About 1,000 Enterprise calls/month (check the current pricing page) | 60 per run × ~10 runs = 600. The **900 cap** is shared between cloud and menu runs (counted in the Sheet's Runs tab), so the tool stops before you go over |
| Google Sheets API             | Free                                                                | —                                                                                                                                                                |

In the Google Cloud console, set up **Billing → Budgets & alerts** with a small budget (e.g. $5) so Google emails you if anything ever starts costing money.

## Good to know

* **Your web filter doesn't apply in the cloud.** Leads marked **Recheck** (blocked by your office network) get checked properly on the next cloud run.
* **Editing while it runs:** the job re-reads the Sheet a few seconds before writing, so your edits are kept. Only an edit made in those same few seconds could be lost.
* **Changing searches or areas:** edit `niches.json`, then run `git add -A`, `git commit -m "update searches"` and `git push`.
* **Pausing it:** go to **Actions → LeadFinder auto run → ⋯ → Disable workflow**.
