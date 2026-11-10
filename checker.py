"""Passive site checks.

Only ordinary public page loads, the same as a visitor's browser makes:
the homepage, the RSS feed, a contact page, /wp-login.php and /xmlrpc.php (GET only).
No login attempts, no form posts, no vulnerability probing.
Findings are worded as observations, never as "your site is hacked".
"""
import datetime as dt
import re
import socket
import ssl
import time
import warnings
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter("ignore", InsecureRequestWarning)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TIMEOUT = 15
TODAY = dt.date.today()

# PHP end of security support (php.net/supported-versions)
PHP_EOL = {
    "5": dt.date(2018, 12, 31), "7.0": dt.date(2019, 1, 10), "7.1": dt.date(2019, 12, 1),
    "7.2": dt.date(2020, 11, 30), "7.3": dt.date(2021, 12, 6), "7.4": dt.date(2022, 11, 28),
    "8.0": dt.date(2023, 11, 26), "8.1": dt.date(2025, 12, 31), "8.2": dt.date(2026, 12, 31),
    "8.3": dt.date(2027, 12, 31), "8.4": dt.date(2028, 12, 31), "8.5": dt.date(2029, 12, 31),
}

SECURITY_HEADERS = {
    "strict-transport-security": "HSTS",
    "content-security-policy": "CSP",
    "x-frame-options": "X-Frame-Options",
    "x-content-type-options": "X-Content-Type-Options",
    "referrer-policy": "Referrer-Policy",
    "permissions-policy": "Permissions-Policy",
}

SOCIAL_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
                "tiktok.com", "youtube.com", "linktr.ee", "wa.me", "whatsapp.com",
                "business.site", "sites.google.com")

_wp_latest = None


def wp_latest():
    """Current WordPress release, from the official API (cached)."""
    global _wp_latest
    if _wp_latest is None:
        try:
            r = requests.get("https://api.wordpress.org/core/version-check/1.7/", timeout=TIMEOUT)
            _wp_latest = r.json()["offers"][0]["current"]
        except Exception:
            _wp_latest = ""
    return _wp_latest


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _get(session, url, retries=1, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("allow_redirects", True)
    try:
        return session.get(url, **kw), None
    except requests.exceptions.SSLError:
        try:
            return session.get(url, verify=False, **kw), "ssl"
        except Exception as e:
            return None, str(e)
    except requests.exceptions.ConnectionError as e:
        if retries:  # DNS lookups on Windows sometimes fail under load: try once more
            time.sleep(2)
            return _get(session, url, retries - 1, **kw)
        return None, str(e)
    except Exception as e:
        return None, str(e)


PLACEHOLDER_PAGES = (
    ("web filter violation", "Blocked by your network's web filter (re-check from another network)"),
    ("parked domain", "Parked domain (no live website)"),
    ("domain is parked", "Parked domain (no live website)"),
    ("this domain is for sale", "Domain for sale page (no live website)"),
    ("account suspended", "Hosting account suspended"),
    ("account has been suspended", "Hosting account suspended"),
    ("index of /", "Bare directory listing instead of a website"),
    ("default web site page", "Server default page instead of a website"),
    ("future home of something quite cool", "Hosting default page instead of a website"),
    ("website coming soon", "'Coming soon' placeholder page"),
    ("under construction", "'Under construction' placeholder page"),
    ("error establishing a database connection", "WordPress database connection error shown to visitors"),
    ("there has been a critical error on this website", "WordPress 'critical error' shown to visitors"),
)


def ssl_info(host):
    out = {"https": False, "ssl_valid": False, "ssl_days_left": "", "ssl_expires": "",
           "ssl_issuer": "", "tls_version": "", "ssl_error": ""}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            out["https"] = True
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                out["tls_version"] = tls.version() or ""
        exp = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").date()
        out.update(ssl_valid=True, ssl_expires=exp.isoformat(),
                   ssl_days_left=(exp - TODAY).days,
                   ssl_issuer=dict(x[0] for x in cert.get("issuer", ())).get("organizationName", ""))
    except ssl.SSLCertVerificationError as e:
        out["ssl_error"] = e.verify_message or str(e)
    except ssl.SSLError as e:
        out["ssl_error"] = f"TLS error: {e.reason or e}"
    except OSError:
        if not out["https"]:
            out["ssl_error"] = "No HTTPS (port 443 closed)"
        else:
            out["ssl_error"] = "TLS handshake failed"
    return out


def pagespeed(url, api_key=""):
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        r = requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                         params=params, timeout=120)
        lh = r.json()["lighthouseResult"]
        score = round(lh["categories"]["performance"]["score"] * 100)
        lcp = lh["audits"].get("largest-contentful-paint", {}).get("displayValue", "")
        return score, lcp
    except Exception:
        return "", ""


# --------------------------------------------------------------------------
# HTML parsing helpers
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_END = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".js", ".css")
BAD_EMAIL_PART = ("example.", "sentry", "wixpress", "domain.com", "yourdomain", "email.com",
                  "@2x", "@3x", "u003e", "organization.org", "company.com", "you@", "name@",
                  "user@", "johndoe", "john.doe", "test@", "wordpress.org", "w3.org")


def _emails(html):
    found = set()
    for m in re.findall(r'mailto:([^"\'?>\s]+)', html, re.I):
        found.add(m.strip().lower())
    for m in EMAIL_RE.findall(html):
        found.add(m.lower())
    return sorted(e for e in found
                  if not e.endswith(BAD_EMAIL_END) and not any(b in e for b in BAD_EMAIL_PART)
                  and len(e) < 60)


def _phones(html):
    phones = set()
    for m in re.findall(r'href=["\']tel:([^"\']+)', html, re.I):
        p = re.sub(r"[^\d+]", "", m)
        if 9 <= len(p.lstrip("+")) <= 13:
            phones.add(p)
    # Kenyan numbers written in plain text: +254 7xx / 07xx / 01xx
    for m in re.findall(r"(?:\+254|\b0)\s?[17]\d{2}[\s-]?\d{3}[\s-]?\d{3}\b", html):
        phones.add(re.sub(r"[^\d+]", "", m))
    return sorted(phones)


def _whatsapp(html):
    nums = set()
    for m in re.findall(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send/?\?phone=)\+?(\d{9,13})", html, re.I):
        nums.add("+" + m)
    return sorted(nums)


def _socials(html):
    out = {}
    for name, pat in (("Facebook", r"facebook\.com/(?!sharer|share|plugins|tr\b|dialog)[A-Za-z0-9_.\-/]+"),
                      ("Instagram", r"instagram\.com/(?!p/|share)[A-Za-z0-9_.]+"),
                      ("LinkedIn", r"linkedin\.com/(?:company|in)/[A-Za-z0-9_\-]+"),
                      ("X", r"(?:twitter|x)\.com/(?!intent|share|home)[A-Za-z0-9_]+"),
                      ("TikTok", r"tiktok\.com/@[A-Za-z0-9_.]+")):
        m = re.search(pat, html, re.I)
        if m:
            out[name] = "https://" + m.group(0).rstrip("/")
    return out


def _contact_link(html, base):
    for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).lower()
        if "contact" in href.lower() or "contact" in text:
            url = urljoin(base, href)
            if urlparse(url).hostname == urlparse(base).hostname:
                return url
    return ""


def _detect_cms(html, headers):
    h = html.lower()
    gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']generator["\']', html, re.I)
    gen = gen.group(1) if gen else ""
    if "/wp-content/" in h or "/wp-includes/" in h or "wordpress" in gen.lower() \
            or "api.w.org" in headers.get("link", ""):
        return "WordPress", gen
    for name, marks in (("Joomla", ("joomla", "/media/jui/")), ("Drupal", ("drupal",)),
                        ("Wix", ("wix.com", "wixstatic")), ("Squarespace", ("squarespace",)),
                        ("Shopify", ("cdn.shopify.com",)), ("Webflow", ("webflow",)),
                        ("Blogger", ("blogger.com",)), ("Laravel", ("laravel_session",)),
                        ("Next.js", ("/_next/",))):
        if any(m in h or m in gen.lower() or m in str(headers).lower() for m in marks):
            return name, gen
    return ("Other: " + gen) if gen else "Unknown", gen


def _wp_version(html, gen):
    m = re.search(r"WordPress\s+([\d.]+)", gen)
    if m:
        return m.group(1), "generator tag"
    # Only core files that carry the WordPress version itself (jQuery etc. carry their own)
    vers = re.findall(r"/wp-includes/(?:css/dist/block-library/style(?:\.min)?\.css|js/wp-emoji-release\.min\.js|"
                      r"js/wp-embed\.min\.js|js/comment-reply\.min\.js|css/dashicons\.min\.css)\?ver=(\d+\.\d+(?:\.\d+)?)", html)
    if vers:
        return Counter(vers).most_common(1)[0][0], "core file versions"
    return "", ""


def _plugins(html):
    plugins = {}
    for name, ver in re.findall(r"/wp-content/plugins/([a-zA-Z0-9_\-]+)/[^\"'\s]*?(?:\?ver=([\d.]+))?[\"'\s]", html):
        if name not in plugins or (ver and not plugins[name]):
            plugins[name] = ver
    return plugins


REAL_ESTATE_THEMES = ("wpresidence", "houzez", "realhomes", "myhome", "homey", "homeo", "real-estate-7",
                      "realestate-7", "reales", "findeo", "wpcasa", "zoner", "realia", "homeland", "resideo",
                      "houzing", "rentex", "luxus", "realtyspace", "realtor", "estate", "property", "realty")
REAL_ESTATE_PLUGINS = ("estatik", "essential-real-estate", "easy-property-listings", "wp-property", "opalestate",
                       "wpresidence-core", "houzez", "myhome-core", "realhomes", "propertyhive", "real-estate",
                       "wpcasa", "ere-", "property", "realty", "wp-listings", "impress-listings", "idx")
REAL_ESTATE_WORDS = ("real estate", "property management", "properties for sale", "property for sale",
                     "houses for sale", "apartments for rent", "for rent", "for sale", "letting", "landlord",
                     "tenant", "bedroom", "serviced apartment", "property developer", "acre", "sq ft")


def _real_estate_stack(theme, plugins):
    t = (theme or "").lower()
    if any(k in t for k in REAL_ESTATE_THEMES):
        return f"Real estate theme: {theme}"
    hit = next((p for p in plugins if any(k in p.lower() for k in REAL_ESTATE_PLUGINS)), "")
    return f"Real estate plugin: {hit}" if hit else ""


def _theme(html):
    m = Counter(re.findall(r"/wp-content/themes/([a-zA-Z0-9_\-]+)/", html)).most_common(1)
    return m[0][0] if m else ""


# --------------------------------------------------------------------------
# Main check
# --------------------------------------------------------------------------
def check_site(website, do_pagespeed=False, pagespeed_key=""):
    r = {"checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "findings": []}
    url = website.strip()
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    r["domain"] = host[4:] if host.startswith("www.") else host

    if any(host == s or host.endswith("." + s) for s in SOCIAL_HOSTS):
        r.update(reachable="Social page only", cms="None (social page)")
        r["findings"].append((3, "Business has no website of its own, only a social or listing page"))
        return _score(r)

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})

    # --- TLS / certificate
    r.update(ssl_info(host))

    # --- Homepage
    t0 = time.time()
    resp, err = _get(s, url)
    if resp is None and url.startswith("https://"):
        resp, err = _get(s, "http://" + host)
    r["response_s"] = round(time.time() - t0, 2)
    if resp is None:
        err = err or ""
        r["error"] = err[:200]
        if "NameResolution" in err or "getaddrinfo" in err:
            r["reachable"] = "Domain not resolving"
            r["findings"].append((3, "Domain does not resolve (may have expired, or the listing has the wrong address)"))
        elif "timed out" in err.lower():
            r["reachable"] = "Timed out"
            r["findings"].append((2, "Website did not respond within 15s when checked (very slow, down, or blocking automated visits)"))
        else:
            r["reachable"] = "Down / unreachable"
            r["findings"].append((3, "Website did not load when checked (could be down or misconfigured)"))
        return _score(r)
    r["reachable"] = f"Yes ({resp.status_code})" if resp.status_code < 400 else f"Error {resp.status_code}"
    r["final_url"] = resp.url
    html = resp.text[:2_000_000] if "html" in resp.headers.get("content-type", "html") else ""
    hdr = {k.lower(): v for k, v in resp.headers.items()}
    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1].lower()
    body = re.sub(r"<[^>]+>", " ", html[:20000]).lower()
    body_marks = ("error establishing", "critical error", "account suspended", "account has been suspended", "parked")
    r["site_state"] = next((label for mark, label in PLACEHOLDER_PAGES
                            if mark in title or (mark.startswith(body_marks) and mark in body)), "Live")
    if r["site_state"].startswith("Blocked by your network"):
        # What we saw is our own network's block page, not the site: don't score it
        r["reachable"] = "Blocked by web filter"
        r["findings"] = []
        out = _score(r)
        out["priority"] = "Recheck"
        out["top_findings"] = "- Could not check: blocked by your network's web filter. Rescan from another network."
        return out
    if r["site_state"] != "Live":
        r["findings"].append((3, f"Homepage shows: {r['site_state']}"))

    # --- HTTP -> HTTPS redirect
    if r["https"]:
        http_resp, _ = _get(s, "http://" + host, timeout=10)
        r["http_to_https"] = "Yes" if http_resp is not None and http_resp.url.startswith("https://") else "No"
    else:
        r["http_to_https"] = "No"

    # --- Server / PHP
    r["server"] = hdr.get("server", "")
    powered = hdr.get("x-powered-by", "") + " " + hdr.get("server", "")
    m = re.search(r"PHP/(\d+\.\d+(?:\.\d+)?)", powered)
    r["php_version"] = m.group(1) if m else ""
    r["php_source"] = "X-Powered-By header" if m else ""

    # --- CMS / WordPress
    cms, gen = _detect_cms(html, hdr)
    r["cms"] = cms
    if cms == "WordPress":
        ver, src = _wp_version(html, gen)
        if not ver:
            feed, _ = _get(s, urljoin(resp.url, "/feed/"), timeout=10)
            if feed is not None:
                m = re.search(r"wordpress\.org/\?v=([\d.]+)", feed.text)
                if m:
                    ver, src = m.group(1), "RSS feed"
        r["wp_version"], r["wp_version_source"] = ver, src
        r["wp_latest"] = wp_latest()
        r["theme"] = _theme(html)
        plugins = _plugins(html)
        r["plugin_count"] = len(plugins)
        r["plugins"] = ", ".join(f"{k} {v}".strip() for k, v in sorted(plugins.items()))

        r["niche"] = _real_estate_stack(r["theme"], plugins)

        lg, _ = _get(s, urljoin(resp.url, "/wp-login.php"), timeout=10)
        r["wp_login_exposed"] = "Yes" if lg is not None and lg.status_code == 200 and "user_login" in lg.text else "No"
        xr, _ = _get(s, urljoin(resp.url, "/xmlrpc.php"), timeout=10)
        r["xmlrpc_enabled"] = "Yes" if xr is not None and "XML-RPC server accepts POST requests only" in xr.text else "No"

    if not r.get("niche"):
        text = re.sub(r"<[^>]+>", " ", html).lower()
        hits = sum(1 for k in REAL_ESTATE_WORDS if k in text)
        r["niche"] = "Real estate (site content)" if hits >= 3 else ""

    # --- Security headers
    missing = [name for k, name in SECURITY_HEADERS.items() if k not in hdr]
    r["missing_headers"] = ", ".join(missing)
    r["missing_header_count"] = len(missing)

    # --- Mixed content & copyright year
    r["mixed_content"] = "Yes" if resp.url.startswith("https://") and re.search(
        r'<(?:script|img|link|iframe)[^>]+(?:src|href)=["\']http://', html, re.I) else "No"
    years = [int(y) for y in re.findall(r"(?:©|&copy;|&#169;|copyright)\s*(?:\d{4}\s*(?:-|–|&#8211;|&ndash;)\s*)?(\d{4})", html, re.I)]
    years = [y for y in years if 1995 <= y <= TODAY.year + 1]
    r["copyright_year"] = max(years) if years else ""

    # --- Contacts (homepage + contact page)
    contact_html = html
    cl = _contact_link(html, resp.url)
    if cl:
        cr, _ = _get(s, cl, timeout=10)
        if cr is not None and cr.status_code < 400:
            contact_html += cr.text[:1_000_000]
            r["contact_page"] = cl
    r["emails"] = ", ".join(_emails(contact_html)[:5])
    r["site_phones"] = ", ".join(_phones(contact_html)[:4])
    r["whatsapp"] = ", ".join(_whatsapp(contact_html)[:2])
    r["socials"] = " | ".join(_socials(contact_html).values())
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    r["page_title"] = re.sub(r"\s+", " ", t.group(1)).strip()[:120] if t else ""

    # --- PageSpeed (optional, slow)
    if do_pagespeed:
        r["pagespeed_mobile"], r["lcp"] = pagespeed(resp.url, pagespeed_key)

    _findings(r)
    return _score(r)


def _findings(r):
    f = r["findings"]
    # HTTPS / certificate
    if not r.get("https"):
        f.append((3, "No HTTPS: the site loads without a secure (padlock) connection"))
    elif "local issuer" in (r.get("ssl_error") or ""):
        f.append((2, "SSL certificate chain is incomplete (some phones and browsers may show a security warning)"))
    elif not r.get("ssl_valid"):
        f.append((3, f"SSL certificate problem: {r.get('ssl_error')}"))
    else:
        d = r.get("ssl_days_left")
        if isinstance(d, int) and d < 14:
            f.append((3, f"SSL certificate expires in {d} days ({r['ssl_expires']})"))
        elif isinstance(d, int) and d < 30:
            f.append((2, f"SSL certificate expires in {d} days ({r['ssl_expires']})"))
        if r.get("http_to_https") == "No":
            f.append((2, "Visitors typing http:// are not redirected to the secure https:// version"))

    # PHP
    pv = r.get("php_version")
    if pv:
        major_minor = ".".join(pv.split(".")[:2])
        eol = PHP_EOL.get(major_minor) or PHP_EOL.get(pv.split(".")[0])
        if eol and eol < TODAY:
            f.append((3, f"Server reports PHP {pv} (X-Powered-By header); PHP {major_minor} stopped getting security fixes on {eol:%d %b %Y}"))
        elif eol and (eol - TODAY).days < 120:
            f.append((2, f"Server reports PHP {pv}; security support for PHP {major_minor} ends {eol:%d %b %Y}"))
        else:
            f.append((1, f"Server publicly shows its PHP version ({pv}) in response headers"))

    # WordPress
    wv, latest = r.get("wp_version"), r.get("wp_latest")
    if wv and latest:
        a, b = _vtuple(wv) + (0, 0), _vtuple(latest) + (0, 0)
        # WordPress major releases go 6.8 -> 6.9 -> 7.0, so x.y counts as one step
        behind = (b[0] * 10 + b[1]) - (a[0] * 10 + a[1])
        if behind >= 3:
            f.append((3, f"WordPress {wv} detected ({r['wp_version_source']}); current release is {latest}, {behind} major releases behind"))
        elif behind >= 1:
            f.append((2, f"WordPress {wv} detected ({r['wp_version_source']}); current release is {latest}"))
        elif a < b:
            f.append((1, f"WordPress {wv} is missing the latest maintenance/security release ({latest})"))
    if r.get("xmlrpc_enabled") == "Yes":
        f.append((2, "xmlrpc.php is enabled (commonly used for password-guessing attacks if left open)"))
    if r.get("wp_login_exposed") == "Yes":
        f.append((1, "Login page is at the default /wp-login.php address"))
    if r.get("plugin_count", 0) >= 20:
        f.append((1, f"{r['plugin_count']} plugins visible on the homepage alone (more to keep updated)"))

    # Headers, content, speed
    if r.get("missing_header_count", 0) >= 4:
        f.append((2, f"Missing {r['missing_header_count']} of 6 recommended security headers ({r['missing_headers']})"))
    if r.get("mixed_content") == "Yes":
        f.append((2, "Secure page loads some images/scripts over insecure http:// (mixed content)"))
    rt = r.get("response_s")
    if isinstance(rt, (int, float)) and rt > 4:
        f.append((2, f"Homepage took {rt}s to load when checked (slow)"))
    ps = r.get("pagespeed_mobile")
    if isinstance(ps, int) and ps < 50:
        f.append((2, f"Google PageSpeed mobile score {ps}/100" + (f", main content shows after {r['lcp']}" if r.get("lcp") else "")))
    cy = r.get("copyright_year")
    if isinstance(cy, int) and cy < TODAY.year - 1:
        f.append((1, f"Footer copyright says {cy}, which suggests the site has not been maintained recently"))


WEIGHTS = {3: 25, 2: 10, 1: 4}


def _score(r):
    r["findings"].sort(key=lambda x: -x[0])
    score = sum(WEIGHTS[sev] for sev, _ in r["findings"])
    r["score"] = min(score, 100)
    r["priority"] = "Hot" if score >= 50 else "Warm" if score >= 20 else "Cold"
    r["top_findings"] = "\n".join(f"- {text}" for _, text in r["findings"][:3])
    r["all_findings"] = "\n".join(f"[{'HML'[3 - sev]}] {text}" for sev, text in r["findings"])
    r["finding_count"] = len(r["findings"])
    del r["findings"]
    return r
