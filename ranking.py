"""Free ranking signal and competitor discovery.

SEMrush/Ahrefs/SimilarWeb APIs are paid, so the free stand-in for how strong a
site is in search comes from Open PageRank (domcop.com/openpagerank), which is free
with a sign-up key. It returns a 0-10 authority score and a global rank, built from
the Common Crawl link graph. Bigger number = more sites link to it = usually ranks
better on Google.
"""
import re

import requests

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def open_pagerank(domains, api_key):
    """{domain: (authority 0-10, global rank)} for up to any number of domains."""
    out = {}
    domains = [d for d in dict.fromkeys(domains) if d]
    for i in range(0, len(domains), 100):
        batch = domains[i:i + 100]
        try:
            r = requests.get("https://openpagerank.com/api/v1.0/getPageRank",
                             params=[("domains[]", d) for d in batch],
                             headers={"API-OPR": api_key}, timeout=TIMEOUT)
            for row in r.json().get("response", []):
                if row.get("status_code") == 200:
                    out[row["domain"]] = (round(float(row.get("page_rank_decimal") or 0), 2),
                                          int(row["rank"]) if str(row.get("rank") or "").isdigit() else "")
        except Exception as e:
            print(f"  (Open PageRank lookup failed: {e})")
    return out


def add_authority(results, api_key):
    if not api_key:
        return results
    print("Looking up authority scores (Open PageRank)...")
    ranks = open_pagerank([r.get("domain", "") for r in results], api_key)
    for r in results:
        if r.get("domain") in ranks:
            r["authority"], r["global_rank"] = ranks[r["domain"]]
    return results


def read_site_profile(url, niches, area_pool):
    """Look at a seed website and work out which niche it is in and which areas it serves."""
    if "://" not in url:
        url = "https://" + url
    try:
        html = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT).text
    except requests.exceptions.SSLError:
        html = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, verify=False).text
    text = re.sub(r"<[^>]+>", " ", html).lower()
    text = re.sub(r"\s+", " ", text)

    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
    blocked = any(b in title.lower() for b in ("web filter", "access denied", "blocked", "forbidden",
                                                "just a moment", "attention required"))
    scores = {k: sum(text.count(w) for w in n.get("keywords", [])) for k, n in niches.items()}
    best = max(scores, key=scores.get)
    # Weak or blocked reads are not trusted: the caller falls back to the main niche
    niche = None if blocked or scores[best] < 3 else best

    areas = []
    for a in area_pool:
        name = a.split(",")[0].strip().lower()
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            areas.append(a)
    return {
        "niche": niche,
        "niche_scores": scores,
        "areas": areas,
        "title": title,
        "blocked": blocked,
    }
