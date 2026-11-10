"""Where leads come from: Google Places API, OpenStreetMap, or your own list."""
import csv
import datetime as dt
import json
import os
import time
from urllib.parse import urlparse

import requests

UA = "LeadFinder/1.0 (python-requests)"
TIMEOUT = 30


def domain_of(url):
    """'https://www.Example.co.ke/about' -> 'example.co.ke'."""
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _lead(**kw):
    base = {
        "business": "", "website": "", "domain": "", "address": "", "maps_phone": "",
        "maps_url": "", "rating": "", "reviews": "", "category": "", "source": "",
        "maps_emails": "",
    }
    base.update({k: v for k, v in kw.items() if v is not None})
    base["domain"] = domain_of(base["website"])
    return base


# --------------------------------------------------------------------------
# Google Places API (New) - Text Search. Best data, needs an API key.
# Every page of results is one billable call, so calls are counted per month
# in usage.json and searches stop at the cap set in config.json.
# --------------------------------------------------------------------------
USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.json")


def _month():
    return dt.date.today().strftime("%Y-%m")


# When the Google Sheet tracker is used, the month's earlier calls come from its Runs tab
# (shared by local and cloud runs) instead of usage.json.
_baseline = None
session_calls = 0


def use_shared_count(calls_already_used):
    global _baseline
    _baseline = calls_already_used


def google_calls_used():
    if _baseline is not None:
        return _baseline + session_calls
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            return json.load(f).get(_month(), {}).get("places_calls", 0)
    except (OSError, ValueError):
        return 0


def _count_google_call():
    global session_calls
    session_calls += 1
    if _baseline is not None:
        return
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    m = data.setdefault(_month(), {})
    m["places_calls"] = m.get("places_calls", 0) + 1
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class BudgetReached(Exception):
    pass


def google_places(category, area, api_key, limit=60, cap=900):
    query = f"{category} in {area}" if area else category
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join([
            "places.displayName", "places.formattedAddress", "places.websiteUri",
            "places.nationalPhoneNumber", "places.internationalPhoneNumber",
            "places.googleMapsUri", "places.rating", "places.userRatingCount",
            "places.businessStatus", "places.primaryTypeDisplayName", "nextPageToken",
        ]),
    }
    body = {"textQuery": query, "pageSize": 20}
    leads = []
    while len(leads) < limit:
        if google_calls_used() >= cap:
            if leads:
                break
            raise BudgetReached(f"Monthly Google call cap reached ({cap}). Raise google_monthly_call_cap in config.json or wait for next month.")
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        _count_google_call()
        if r.status_code != 200:
            raise RuntimeError(f"Google Places error {r.status_code}: {r.text[:300]}")
        data = r.json()
        for p in data.get("places", []):
            if p.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            leads.append(_lead(
                business=(p.get("displayName") or {}).get("text", ""),
                website=p.get("websiteUri", ""),
                address=p.get("formattedAddress", ""),
                maps_phone=p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber", ""),
                maps_url=p.get("googleMapsUri", ""),
                rating=p.get("rating", ""),
                reviews=p.get("userRatingCount", ""),
                category=(p.get("primaryTypeDisplayName") or {}).get("text", "") or category,
                source=f"Google: {query}",
            ))
        token = data.get("nextPageToken")
        if not token:
            break
        body = {"textQuery": query, "pageSize": 20, "pageToken": token}
        time.sleep(1)
    return leads[:limit]


# --------------------------------------------------------------------------
# OpenStreetMap (free, no key). Fewer businesses list a website, but it works.
# --------------------------------------------------------------------------
OSM_TAGS = {
    "hotel": ['"tourism"~"^(hotel|guest_house|hostel|motel|apartment)$"'],
    "lodge": ['"tourism"~"^(hotel|guest_house|chalet|camp_site)$"'],
    "restaurant": ['"amenity"~"^(restaurant|cafe|fast_food|bar|pub)$"'],
    "cafe": ['"amenity"="cafe"'],
    "school": ['"amenity"~"^(school|college|university|kindergarten)$"'],
    "college": ['"amenity"~"^(college|university)$"'],
    "hospital": ['"amenity"~"^(hospital|clinic|doctors|dentist)$"'],
    "clinic": ['"amenity"~"^(hospital|clinic|doctors|dentist)$"'],
    "pharmacy": ['"amenity"="pharmacy"'],
    "church": ['"amenity"="place_of_worship"'],
    "bank": ['"amenity"~"^(bank|bureau_de_change)$"'],
    "shop": ['"shop"'],
    "salon": ['"shop"~"^(hairdresser|beauty)$"'],
    "car": ['"shop"~"^(car|car_repair|car_parts)$"'],
    "real estate": ['"office"~"^(estate_agent|property_management)$"'],
    "property": ['"office"~"^(estate_agent|property_management)$"'],
    "apartment": ['"tourism"="apartment"'],
    "lawyer": ['"office"="lawyer"'],
    "office": ['"office"'],
    "travel": ['"shop"="travel_agency"', '"office"="travel_agent"'],
    "tour": ['"shop"="travel_agency"', '"office"="travel_agent"', '"tourism"="information"'],
    "ngo": ['"office"~"^(ngo|association|foundation|charity)$"'],
    "gym": ['"leisure"="fitness_centre"'],
}


def _osm_filters(category):
    c = category.lower().strip()
    for key, tags in OSM_TAGS.items():
        if key in c or c.rstrip("s") in key:
            return tags
    # Unknown category: match the word in the business name
    word = c.rstrip("s").replace('"', "")
    return [f'"name"~"{word}",i']


def osm(category, area, limit=60):
    if not area:
        raise ValueError("OpenStreetMap search needs --area, e.g. --area \"Westlands, Nairobi\"")
    g = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": area, "format": "json", "limit": 1},
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    g.raise_for_status()
    hits = g.json()
    if not hits:
        raise RuntimeError(f"OpenStreetMap could not find the area '{area}'")
    s, n, w, e = (float(x) for x in hits[0]["boundingbox"])
    bbox = f"{s},{w},{n},{e}"
    parts = []
    for f in _osm_filters(category):
        for web in ("website", "contact:website", "url"):
            parts.append(f'nwr[{f}]["{web}"]({bbox});')
    q = f"[out:json][timeout:90];({''.join(parts)});out tags center;"
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q},
                      headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    leads, seen = [], set()
    for el in r.json().get("elements", []):
        t = el.get("tags", {})
        site = t.get("website") or t.get("contact:website") or t.get("url", "")
        if not site or site in seen:
            continue
        seen.add(site)
        addr = ", ".join(x for x in (t.get("addr:street"), t.get("addr:suburb"), t.get("addr:city")) if x)
        leads.append(_lead(
            business=t.get("name", ""),
            website=site,
            address=addr or area,
            maps_phone=t.get("phone") or t.get("contact:phone", ""),
            maps_emails=t.get("email") or t.get("contact:email", ""),
            category=category,
            source=f"OSM: {category} / {area}",
        ))
        if len(leads) >= limit:
            break
    return leads


# --------------------------------------------------------------------------
# Your own list: a .txt (one URL per line) or a .csv with a website/url column.
# --------------------------------------------------------------------------
def from_urls(urls, category="", source="Manual"):
    return [_lead(website=u.strip(), category=category, source=source)
            for u in urls if u.strip() and not u.strip().startswith("#")]


def from_file(path, category=""):
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        leads = []
        for row in rows:
            low = {k.lower().strip(): (v or "").strip() for k, v in row.items() if k}
            site = low.get("website") or low.get("url") or low.get("domain") or low.get("site", "")
            if not site:
                continue
            leads.append(_lead(
                website=site,
                business=low.get("business") or low.get("name", ""),
                maps_phone=low.get("phone", ""),
                address=low.get("address", ""),
                category=low.get("category", category),
                source=f"File: {path}",
            ))
        return leads
    with open(path, encoding="utf-8-sig") as f:
        return from_urls(f.read().splitlines(), category, source=f"File: {path}")
