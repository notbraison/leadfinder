"""LeadFinder: find business websites, run passive checks, keep an Excel lead list.

Double-click run.bat (or run it with no options) for the numbered menu.

Command-line use (for scheduled runs):
  python leadfinder.py sweep property                 # niche preset from niches.json
  python leadfinder.py sweep property --pages 2 --areas "Kilimani, Nairobi" "Karen, Nairobi"
  python leadfinder.py competitors sirfrancismarketingltd.co.ke
  python leadfinder.py find "hotels" --area "Westlands, Nairobi"
  python leadfinder.py check example.co.ke another.com
  python leadfinder.py check --file mylist.txt --category "past clients"
  python leadfinder.py rescan --status New
"""
import argparse
import datetime as dt
import json
import os
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

import checker
import ranking
import sources
import tracker

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "leads.xlsx")

DEFAULT_SKIP = [
    "hilton.com", "marriott.com", "accor.com", "fairmont.com", "bestwestern.com", "radissonhotels.com",
    "ihg.com", "hyatt.com", "kempinski.com", "sarovahotels.com", "serenahotels.com", "booking.com",
    "tripadvisor.com", "airbnb.com", "google.com", "jumia.co.ke", "safaricom.co.ke",
    "buyrentkenya.com", "property24.co.ke", "jiji.co.ke", "knightfrank.co.ke", "knightfrank.com",
]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def config():
    cfg = {}
    p = os.path.join(HERE, "config.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg = {k: ("" if isinstance(v, str) and v.startswith("PASTE") else v) for k, v in cfg.items()}
    return {
        "google_key": os.environ.get("GOOGLE_PLACES_API_KEY") or cfg.get("google_places_api_key", ""),
        "pagespeed_key": os.environ.get("PAGESPEED_API_KEY") or cfg.get("pagespeed_api_key", "")
                         or cfg.get("google_places_api_key", ""),
        "opr_key": os.environ.get("OPEN_PAGERANK_API_KEY") or cfg.get("open_pagerank_api_key", ""),
        "cap": int(cfg.get("google_monthly_call_cap", 900)),
        "skip": DEFAULT_SKIP + [d.lower() for d in cfg.get("skip_domains", [])],
        "sheet_id": os.environ.get("LEADS_SHEET_ID") or cfg.get("google_sheet_id", ""),
        "sa_file": os.path.join(HERE, cfg.get("google_service_account_file", "service-account.json")),
        "sa_json": os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        "auto_calls": int(os.environ.get("AUTO_CALLS_PER_RUN") or cfg.get("auto_calls_per_run", 60)),
        "auto_interval": int(os.environ.get("AUTO_INTERVAL_DAYS") or cfg.get("auto_interval_days", 3)),
    }


# --------------------------------------------------------------------------
# Where leads are kept: Google Sheet (if google_sheet_id is set) or leads.xlsx
# --------------------------------------------------------------------------
class ExcelStore:
    def __init__(self, path):
        self.path = path
        self.label = os.path.basename(path)

    def merge_and_save(self, records, note, google_calls=0, where="local"):
        return tracker.merge_and_save(self.path, records, note, google_calls, where)

    def domains(self, status=None, older_than_days=0, limit=None):
        out = tracker.domains(self.path, status)
        return out[:limit] if limit else out

    def count(self):
        try:
            return len(tracker.load(self.path)[0])
        except Exception:
            return 0

    def open(self):
        if os.path.exists(self.path):
            os.startfile(self.path)
        else:
            print("No spreadsheet yet. Run a search first.")


def get_store(cfg, out):
    if cfg["sheet_id"] and out == DEFAULT_OUT:
        import gsheet
        store = gsheet.from_config(cfg["sheet_id"], cfg["sa_file"], cfg["sa_json"])
        store.label = "Google Sheet"
        store.open = lambda: webbrowser.open(store.url)
        sources.use_shared_count(store.calls_this_month())
        return store
    return ExcelStore(out)


_logged_calls = 0


def save(store, results, note):
    """Write results and log how many Google calls this run used."""
    global _logged_calls
    calls = sources.session_calls - _logged_calls
    _logged_calls = sources.session_calls
    where = "cloud" if os.environ.get("GITHUB_ACTIONS") else "local"
    return store.merge_and_save(results, note, calls, where)


def niches():
    with open(os.path.join(HERE, "niches.json"), encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Core steps
# --------------------------------------------------------------------------
def dedupe(leads, skip=()):
    seen, out = set(), []
    for l in leads:
        key = l["domain"] or "noweb:" + l["business"].lower()
        if key in seen or any(l["domain"] == s or l["domain"].endswith("." + s) for s in skip):
            continue
        seen.add(key)
        out.append(l)
    return out


def run_checks(leads, pagespeed=False, pagespeed_key="", workers=6):
    with_site = [l for l in leads if l["domain"]]
    no_site = [l for l in leads if not l["domain"]]
    results = []
    if with_site:
        print(f"Checking {len(with_site)} websites (passive checks only)...")
        workers = min(workers, 4) if pagespeed else workers
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(checker.check_site, l["website"], pagespeed, pagespeed_key): l for l in with_site}
            for i, fut in enumerate(as_completed(futs), 1):
                lead = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # never let one site kill the run
                    res = {"domain": lead["domain"], "reachable": "Check failed", "error": str(e)[:200],
                           "score": 0, "priority": "Cold", "top_findings": "", "all_findings": ""}
                rec = {**lead, **res}
                results.append(rec)
                print(f"  [{i:>3}/{len(with_site)}] {rec.get('priority', ''):<4} {rec.get('score', 0):>3}  "
                      f"{lead['domain']:<35} {rec.get('cms', '')} {rec.get('wp_version', '')}"
                      f"{'  [' + rec['niche'] + ']' if rec.get('niche') else ''}")
    for l in no_site:
        results.append({**l, "score": 15, "priority": "Cold", "reachable": "No website listed"})
    return results


def finish(cfg, leads, out, note, pagespeed=False, no_check=False):
    leads = dedupe(leads, cfg["skip"])
    print(f"Found {len(leads)} businesses, {sum(1 for l in leads if l['domain'])} with a website.")
    if not leads:
        return
    results = leads if no_check else run_checks(leads, pagespeed, cfg["pagespeed_key"])
    ranking.add_authority(results, cfg["opr_key"])
    path, added, updated = save(out, results, note)
    hot = sum(1 for r in results if r.get("priority") == "Hot")
    warm = sum(1 for r in results if r.get("priority") == "Warm")
    print(f"\nDone. {len(results)} leads ({hot} hot, {warm} warm). {added} new, {updated} updated.")
    print(f"Saved to {path}")


def search(cfg, query, area, pages=3, source=None):
    """One search. Returns leads (may be empty). Google if a key is set, else OpenStreetMap."""
    src = source or ("google" if cfg["google_key"] else "osm")
    try:
        if src == "google":
            return sources.google_places(query, area, cfg["google_key"], pages * 20, cfg["cap"])
        return sources.osm(query, area, pages * 20)
    except sources.BudgetReached:
        raise
    except Exception as e:
        print(f"  ! {query} / {area}: {e}")
        return []


def do_find(cfg, out, category, area, source=None, limit=60, pagespeed=False, no_check=False):
    src = source or ("google" if cfg["google_key"] else "osm")
    where = f" in {area}" if area else ""
    print(f"Searching {'Google Places' if src == 'google' else 'OpenStreetMap'} for {category}{where}...")
    leads = search(cfg, category, area, max(1, -(-limit // 20)), src)[:limit]
    finish(cfg, leads, out, f"find '{category}'{where} via {src}", pagespeed, no_check)


def do_sweep(cfg, out, niche_key, areas=None, pages=1, pagespeed=False, queries=None, note=None):
    data = niches()["niches"]
    if niche_key not in data:
        sys.exit(f"Unknown niche '{niche_key}'. Choose from: {', '.join(data)}")
    n = data[niche_key]
    areas = areas or n["areas"]
    google = bool(cfg["google_key"])
    # OpenStreetMap has one tag set per niche, so one search per area is enough
    queries = queries or (n["queries"] if google else [n.get("osm_category", n["queries"][0])])
    leads = []
    total = len(queries) * len(areas)
    print(f"Sweeping '{n['label']}': {len(queries)} searches x {len(areas)} areas via "
          f"{'Google Places' if google else 'OpenStreetMap'}")
    try:
        for i, area in enumerate(areas):
            for j, q in enumerate(queries):
                found = search(cfg, q, area, pages)
                for l in found:
                    l["category"] = l.get("category") or q
                    if not google:
                        l["source"] = f"OSM: {n['label']} / {area}"
                leads += found
                print(f"  [{i * len(queries) + j + 1:>3}/{total}] {q} - {area}: {len(found)}")
    except sources.BudgetReached as e:
        print(f"\n! {e}\n  Keeping what was found so far.")
    finish(cfg, leads, out, note or f"sweep '{n['label']}' ({len(areas)} areas, {pages} page(s))", pagespeed)


def do_competitors(cfg, out, seed, pages=1, pagespeed=False, max_areas=6, niche=None):
    data = niches()
    print(f"Reading {seed} to work out its niche and areas...")
    try:
        prof = ranking.read_site_profile(seed, data["niches"], data["area_pool"])
    except Exception as e:
        print(f"  Could not load {seed} ({e}).")
        prof = {"niche": None, "areas": [], "title": "", "blocked": True}
    if prof.get("blocked"):
        print("  The site could not be read (blocked by a web filter or bot protection).")
    if not prof["niche"]:
        prof["niche"] = niche or "property"
        print(f"  Niche not clear from the site, using '{prof['niche']}'.")
    n = data["niches"][prof["niche"]]
    areas = prof["areas"][:max_areas] or n["areas"][:max_areas]
    print(f"  Site title : {prof['title'][:80]}")
    print(f"  Niche      : {n['label']}")
    print(f"  Areas found: {', '.join(prof['areas']) or '(none named on the homepage, using niche defaults)'}")
    if len(prof["areas"]) > max_areas:
        print(f"  Using the first {max_areas} areas.")
    cfg = dict(cfg, skip=cfg["skip"] + [sources.domain_of(seed)])
    do_sweep(cfg, out, prof["niche"], areas, pages, pagespeed, note=f"competitors of {seed}")


def do_check(cfg, out, urls, file=None, category="", pagespeed=False):
    leads = sources.from_urls(urls, category)
    if file:
        leads += sources.from_file(file, category)
    leads = dedupe(leads)
    if not leads:
        print("No websites given.")
        return
    results = run_checks(leads, pagespeed, cfg["pagespeed_key"])
    ranking.add_authority(results, cfg["opr_key"])
    path, added, updated = save(out, results, f"check {len(leads)} sites")
    print(f"\nDone. {added} new, {updated} updated. Saved to {path}")


def do_rescan(cfg, out, status=None, pagespeed=False):
    existing = out.domains(status)
    if not existing:
        print("Nothing to rescan.")
        return
    leads = [sources._lead(website=e["website"]) for e in existing]
    results = run_checks(leads, pagespeed, cfg["pagespeed_key"])
    ranking.add_authority(results, cfg["opr_key"])
    path, added, updated = save(out, results, f"rescan{' status=' + status if status else ''}")
    print(f"\nDone. {updated} updated. Saved to {path}")


def rotation(pairs, per_run, interval_days, today=None):
    """Pick this run's slice of (query, area) pairs. Each run moves on to the next slice,
    so scheduled runs walk through every search instead of repeating the same one."""
    if not pairs:
        return []
    today = today or dt.date.today()
    slot = today.toordinal() // max(1, interval_days)
    start = (slot * per_run) % len(pairs)
    return [pairs[(start + i) % len(pairs)] for i in range(min(per_run, len(pairs)))]


def do_auto(cfg, out, niche="property", rescan_limit=120, rescan_older_days=6):
    """The scheduled run: the next slice of the niche across all areas, plus a re-check
    of the New leads that haven't been checked for a while. One save at the end."""
    # GitHub's timer fires daily; only every Nth day is a run day (AUTO_INTERVAL_DAYS).
    # Starting it by hand ("Run workflow") always runs.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and \
            dt.date.today().toordinal() % max(1, cfg["auto_interval"]) != 0:
        print(f"Not a run day (runs every {cfg['auto_interval']} days). Nothing to do.")
        return
    data = niches()
    n = data["niches"][niche]
    areas = data["area_pool"]
    google = bool(cfg["google_key"])
    if google:
        pairs = [(q, a) for a in areas for q in n["queries"]]
        left = cfg["cap"] - sources.google_calls_used()
        per_run = max(0, min(cfg["auto_calls"], left))
    else:
        pairs = [(n.get("osm_category", n["queries"][0]), a) for a in areas]
        per_run = 8
    chosen = rotation(pairs, per_run, cfg["auto_interval"])
    print(f"Auto run: {len(chosen)} of {len(pairs)} searches for '{n['label']}' "
          f"({'Google' if google else 'OpenStreetMap'}; {sources.google_calls_used()}/{cfg['cap']} Google calls used this month)")
    leads = []
    try:
        for i, (q, a) in enumerate(chosen, 1):
            found = search(cfg, q, a, 1)
            for l in found:
                l["category"] = l.get("category") or q
            leads += found
            print(f"  [{i:>3}/{len(chosen)}] {q} - {a}: {len(found)}")
    except sources.BudgetReached as e:
        print(f"! {e}")
    leads = dedupe(leads, cfg["skip"])
    found_domains = {l["domain"] for l in leads}
    old = [sources._lead(website=e["website"]) for e in
           out.domains("New", rescan_older_days, rescan_limit) if e["domain"] not in found_domains]
    print(f"Found {len(leads)} businesses; re-checking {len(old)} older New leads too.")
    results = run_checks(leads + old, False, cfg["pagespeed_key"])
    ranking.add_authority(results, cfg["opr_key"])
    note = (f"auto: {len(chosen)} searches ({chosen[0][1]} .. {chosen[-1][1]}), rescanned {len(old)}"
            if chosen else f"auto: rescanned {len(old)}")
    path, added, updated = save(out, results, note)
    hot_new = [r for r in results if r.get("priority") == "Hot" and r.get("domain") in found_domains]
    print(f"\nDone. {added} new, {updated} updated. Saved to {path}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## LeadFinder auto run\n\n{note}\n\n**{added} new leads, {updated} updated.** "
                    f"Google calls this month: {sources.google_calls_used()}/{cfg['cap']}\n\n")
            if hot_new:
                f.write("| Hot lead | Score | Top finding |\n|---|---|---|\n")
                for r in sorted(hot_new, key=lambda r: -r.get("score", 0))[:25]:
                    top = (r.get("top_findings") or "").split("\n")[0].lstrip("- ").replace("|", "/")
                    f.write(f"| {r.get('business') or r['domain']} ({r['domain']}) | {r.get('score')} | {top} |\n")


# --------------------------------------------------------------------------
# Numbered menu
# --------------------------------------------------------------------------
def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def yes(prompt, default=False):
    v = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not v else v.startswith("y")


def lead_count(store):
    try:
        return store.count()
    except Exception:
        return 0


def pick_areas(default_areas, pool):
    print("\nAreas:")
    print("  Enter = default list: " + "; ".join(default_areas))
    print("  a     = every area in the pool (" + str(len(pool)) + ")")
    print("  or type your own, separated by ;  e.g.  Kilimani, Nairobi; Nyali, Mombasa")
    v = input("Areas: ").strip()
    if not v:
        return default_areas
    if v.lower() == "a":
        return pool
    return [a.strip() for a in v.split(";") if a.strip()]


def pick_pages(cfg, n_searches):
    if not cfg["google_key"]:
        return 1
    used = sources.google_calls_used()
    print(f"\nGoogle results per search: 1 page = 20 businesses, max 3 pages = 60.")
    pages = ask("Pages per search (1-3)", "1")
    pages = max(1, min(3, int(pages))) if pages.isdigit() else 1
    calls = n_searches * pages
    print(f"This uses up to {calls} Google calls. Used this month: {used}/{cfg['cap']}.")
    return pages if yes("Go ahead?", True) else 0


def menu(out):
    while True:
        cfg = config()
        data = niches()
        g = (f"ON  ({sources.google_calls_used()}/{cfg['cap']} calls this month)"
             if cfg["google_key"] else "OFF (using free OpenStreetMap)")
        print("\n" + "=" * 62)
        print("  dotcreative LeadFinder")
        print("=" * 62)
        print(f"  Google Places : {g}")
        print(f"  Authority     : {'ON (Open PageRank)' if cfg['opr_key'] else 'OFF (add open_pagerank_api_key)'}")
        print(f"  Leads kept in : {out.label} ({lead_count(out)} leads)")
        print("-" * 62)
        print("  1  Property management & real estate sweep  (main niche)")
        print("  2  Find competitors of a website")
        print("  3  Sweep another niche (hotels, schools, clinics, law...)")
        print("  4  Search any category in an area")
        print("  5  Check specific websites (type them or use a file)")
        print("  6  Re-check leads already in the sheet")
        print("  7  Open the leads spreadsheet")
        print("  8  Settings and API key status")
        if out.label == "Google Sheet":
            print("  9  Copy leads.xlsx into the Google Sheet (one-time move)")
        print("  0  Exit")
        choice = input("\nChoose a number: ").strip()
        try:
            if choice == "1":
                n = data["niches"]["property"]
                areas = pick_areas(n["areas"], data["area_pool"])
                searches = len(areas) * (len(n["queries"]) if cfg["google_key"] else 1)
                pages = pick_pages(cfg, searches)
                if pages:
                    do_sweep(cfg, out, "property", areas, pages, yes("Also get PageSpeed scores (slower)?"))
            elif choice == "2":
                seed = ask("Website to find competitors for", "sirfrancismarketingltd.co.ke")
                pages = pick_pages(cfg, 6 * len(data["niches"]["property"]["queries"]))
                if pages:
                    do_competitors(cfg, out, seed, pages, yes("Also get PageSpeed scores (slower)?"))
            elif choice == "3":
                keys = [k for k in data["niches"] if k != "property"]
                for i, k in enumerate(keys, 1):
                    print(f"  {i}  {data['niches'][k]['label']}")
                v = ask("Which niche")
                if v.isdigit() and 1 <= int(v) <= len(keys):
                    k = keys[int(v) - 1]
                    n = data["niches"][k]
                    areas = pick_areas(n["areas"], data["area_pool"])
                    searches = len(areas) * (len(n["queries"]) if cfg["google_key"] else 1)
                    pages = pick_pages(cfg, searches)
                    if pages:
                        do_sweep(cfg, out, k, areas, pages, yes("Also get PageSpeed scores (slower)?"))
            elif choice == "4":
                cat = ask("What to search for (e.g. car dealers)")
                area = ask("Area", "Nairobi")
                if cat:
                    pages = pick_pages(cfg, 1)
                    if pages:
                        do_find(cfg, out, cat, area, limit=pages * 20,
                                pagespeed=yes("Also get PageSpeed scores (slower)?"))
            elif choice == "5":
                v = ask("Websites separated by spaces, or a path to a .txt/.csv file")
                if v:
                    cat = ask("Label for these (e.g. past clients)", "")
                    if os.path.isfile(v.strip('"')):
                        do_check(cfg, out, [], v.strip('"'), cat)
                    else:
                        do_check(cfg, out, v.split(), category=cat)
            elif choice == "6":
                print("  1  All leads\n  2  Only leads with Status = New")
                v = ask("Which", "2")
                do_rescan(cfg, out, None if v == "1" else "New", yes("Also get PageSpeed scores (slower)?"))
            elif choice == "7":
                out.open()
            elif choice == "8":
                print(f"\n  Settings file: {os.path.join(HERE, 'config.json')}")
                print(f"  Google Places key : {'set' if cfg['google_key'] else 'not set'}")
                print(f"  Monthly call cap  : {cfg['cap']} (google_monthly_call_cap)")
                print(f"  Open PageRank key : {'set' if cfg['opr_key'] else 'not set'} "
                      "(free at domcop.com/openpagerank)")
                print(f"  Niche presets     : {os.path.join(HERE, 'niches.json')}")
                print(f"  Leads kept in     : {out.label}"
                      + ("" if cfg["sheet_id"] else " (set google_sheet_id to use a Google Sheet)"))
                if yes("Open config.json to edit?"):
                    os.startfile(os.path.join(HERE, "config.json"))
            elif choice == "9" and out.label == "Google Sheet":
                if os.path.exists(DEFAULT_OUT) and yes(
                        f"Replace everything in the Google Sheet with the {lead_count(ExcelStore(DEFAULT_OUT))} leads in leads.xlsx?"):
                    print(f"Copied {out.import_excel(DEFAULT_OUT)} leads.")
            elif choice in ("0", "q", "exit"):
                return
            else:
                print("Please type one of the numbers shown.")
        except KeyboardInterrupt:
            print("\nStopped. Back to the menu.")
        except Exception as e:
            print(f"\n! Something went wrong: {e}")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Find business websites, check them passively, track leads in Excel.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Excel file (default: leads.xlsx)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("sweep", help="Run a niche preset from niches.json across its areas")
    s.add_argument("niche", help="e.g. property, hospitality, schools, health, professional")
    s.add_argument("--areas", nargs="*", help="Override the preset's areas")
    s.add_argument("--pages", type=int, default=1, help="Google pages per search, 1-3 (20 results each)")
    s.add_argument("--pagespeed", action="store_true")

    c = sub.add_parser("competitors", help="Find competitors of a website in the areas it serves")
    c.add_argument("website")
    c.add_argument("--pages", type=int, default=1)
    c.add_argument("--niche", help="Force a niche if the site can't be read, e.g. property")
    c.add_argument("--pagespeed", action="store_true")

    f = sub.add_parser("find", help="Search a category in an area, then check every site found")
    f.add_argument("category")
    f.add_argument("--area", default="")
    f.add_argument("--source", choices=["google", "osm"])
    f.add_argument("--limit", type=int, default=60)
    f.add_argument("--pagespeed", action="store_true")
    f.add_argument("--no-check", action="store_true")

    k = sub.add_parser("check", help="Check sites you already know about")
    k.add_argument("urls", nargs="*")
    k.add_argument("--file")
    k.add_argument("--category", default="")
    k.add_argument("--pagespeed", action="store_true")

    r = sub.add_parser("rescan", help="Re-check sites already in the Excel file")
    r.add_argument("--status")
    r.add_argument("--pagespeed", action="store_true")

    au = sub.add_parser("auto", help="Scheduled run: next slice of searches + re-check older New leads")
    au.add_argument("--niche", default="property")
    au.add_argument("--rescan-limit", type=int, default=120)

    sub.add_parser("sheet-import", help="Copy leads.xlsx into the Google Sheet (replaces its Leads tab)")

    a = ap.parse_args()
    cfg = config()
    a.out = get_store(cfg, a.out)
    if a.cmd is None:
        menu(a.out)
    elif a.cmd == "sweep":
        do_sweep(cfg, a.out, a.niche, a.areas, max(1, min(3, a.pages)), a.pagespeed)
    elif a.cmd == "competitors":
        do_competitors(cfg, a.out, a.website, max(1, min(3, a.pages)), a.pagespeed, niche=a.niche)
    elif a.cmd == "find":
        do_find(cfg, a.out, a.category, a.area, a.source, a.limit, a.pagespeed, a.no_check)
    elif a.cmd == "check":
        do_check(cfg, a.out, a.urls, a.file, a.category, a.pagespeed)
    elif a.cmd == "rescan":
        do_rescan(cfg, a.out, a.status, a.pagespeed)
    elif a.cmd == "auto":
        do_auto(cfg, a.out, a.niche, a.rescan_limit)
    elif a.cmd == "sheet-import":
        if a.out.label != "Google Sheet":
            sys.exit("Set google_sheet_id in config.json first.")
        print(f"Copied {a.out.import_excel(DEFAULT_OUT)} leads into {a.out.url}")


if __name__ == "__main__":
    main()
