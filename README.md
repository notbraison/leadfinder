# LeadFinder

Finds business websites, runs **passive** checks on each one, and keeps everything in `leads.xlsx`.

## Start it

**Double-click `run.bat`.** A numbered menu opens:

```
  1  Property management & real estate sweep  (main niche)
  2  Find competitors of a website
  3  Sweep another niche (hotels, schools, clinics, law...)
  4  Search any category in an area
  5  Check specific websites (type them or use a file)
  6  Re-check leads already in the sheet
  7  Open the leads spreadsheet
  8  Settings and API key status
  0  Exit
```

Type a number and press Enter. Each option asks a few questions; pressing Enter accepts the default shown in [brackets]. Ctrl+C stops a run and takes you back to the menu.

* **1 Property sweep** runs every search in the property preset (property management, real estate agents, developers, serviced apartments, letting agents, valuers) across your chosen areas.
* **2 Competitors** reads a website, works out its niche and the areas it names, then searches for the same kinds of business in those areas. If the site can't be read (for example, your network's web filter blocks it), it uses the property niche and the default areas.
* **3 Other niches:** hotels, schools, clinics, and law/accounting firms.

## Presets: `niches.json`

Edit this file to change the searches, add areas, or add new niches. `area_pool` is the list the competitor finder looks for on a website, and it's also used when you choose **a** (all areas) in a sweep.

## Settings: `config.json`

| Setting | What it does |
|---|---|
| `google_places_api_key` | Turns on Google Places (much better data than OpenStreetMap) and PageSpeed |
| `google_monthly_call_cap` | Searches stop at this many Google calls per month (default 900). Each search page is one call; usage is tracked in `usage.json` |
| `open_pagerank_api_key` | Free key from domcop.com/openpagerank. Adds an **Authority 0-10** score and **Global rank** for each site (how strong the site is in search, like SEMrush's authority score) |
| `skip_domains` | Domains to leave out (big chains, portals) |

Google's free Places allowance is limited (check the current pricing page). The website and phone fields are billed at the Enterprise tier, so the 900 cap keeps you inside roughly 1,000 free calls a month. A full property sweep of 12 areas at 1 page per search is 84 calls.

## Google Sheet and automatic runs every 3 days

See **[CLOUD-SETUP.md](CLOUD-SETUP.md)**. When `google_sheet_id` is set, the menu and the scheduled GitHub runs both use the same Google Sheet instead of `leads.xlsx`. `run.bat auto` is the scheduled run: it picks the next 60 property searches across all areas and re-checks older New leads.

## Command line (for scheduled runs)

```bash
run.bat sweep property
run.bat sweep property --pages 2 --areas "Kilimani, Nairobi" "Karen, Nairobi"
run.bat competitors sirfrancismarketingltd.co.ke
run.bat find "car dealers" --area "Mombasa Road, Nairobi"
run.bat check example.co.ke another.com
run.bat check --file past-clients.txt --category "past clients"
run.bat rescan --status New
```

Add `--pagespeed` to any of them to include the Google PageSpeed mobile score.

## What gets checked (passive only)

The tool only makes the same page loads a normal visitor's browser would: the homepage, the RSS feed, the contact page, and plain GET requests to `/wp-login.php` and `/xmlrpc.php`. It never logs in, never submits forms, and never probes for vulnerabilities.

* Whether the site loads, plus parked, suspended, "coming soon" and WordPress error pages, and domains that no longer resolve
* HTTPS, certificate expiry, and whether http:// redirects to https://
* PHP version when the server reveals it, checked against PHP end-of-life dates
* WordPress version against the current release, theme, visible plugins, xmlrpc.php, and the login page address
* **Niche:** real-estate themes (WpResidence, Houzez, RealHomes, MyHome...) and plugins, or real-estate wording on the site
* Missing security headers, mixed content, load time, PageSpeed (optional), and the footer copyright year
* **Contacts:** emails, phone numbers, WhatsApp links and social profiles

**Score and priority:** high-severity findings add 25 points, medium 10, low 4. **Hot** is 50 or more, **Warm** 20 to 49, **Cold** below 20. **Recheck** means your own network's web filter blocked the site, so it couldn't be checked; rescan it from another network (for example, a phone hotspot).

## The Excel file

* **Gold header columns are yours** (Status dropdown, Next follow-up, Contact person, Last contacted, Notes). Rescans never overwrite them, and columns you add yourself are kept as well.
* **Blue header columns** are refreshed on every scan. A Next follow-up date that is today or earlier turns red. The **Runs** sheet logs every search.
* Close the file in Excel before running; otherwise results go to a timestamped copy.

## Notes

* Use the contact details you collect for genuine one-to-one business outreach, and respect anyone who asks not to be contacted (Kenya's Data Protection Act applies).
* Keep `config.json` private, because it holds your API keys.
