"""HTTP helpers (polite, read-only), ATS adapters and company/slug mapping.

Only PUBLIC job-board endpoints are used: no login, no cookies, no captcha handling.
Supported: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor.
Endpoint shapes are well-known public APIs; the code is written from scratch
(inspired by the providers idea of career-ops, see README).
"""
import datetime, html, json, os, re, threading, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from . import config

TODAY = datetime.date.today().isoformat()

# ---------------------------------------------------------------- polite HTTP
_host_lock, _host_last, _glock = {}, {}, threading.Lock()

def _throttle(host):
    with _glock:
        lk = _host_lock.setdefault(host, threading.Lock())
    with lk:
        wait = _host_last.get(host, 0) + config.get()["min_interval_seconds"] - time.time()
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()

def http_get(url, timeout=30, max_bytes=40_000_000, retries=2):
    """Returns (status, headers, body_bytes|None, error|None). Never sends credentials or cookies."""
    host = urllib.parse.urlparse(url).netloc
    err = None
    for attempt in range(retries + 1):
        _throttle(host)
        req = urllib.request.Request(url, headers={"User-Agent": config.get()["user_agent"], "Accept": "application/json, text/xml, text/html;q=0.8, */*;q=0.5"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read(max_bytes), None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                try:
                    ra = int(e.headers.get("Retry-After", "5"))
                except Exception:
                    ra = 5
                time.sleep(min(ra, 20)); continue
            return e.code, dict(e.headers or {}), None, f"HTTP {e.code}"
        except Exception as e:  # timeout, DNS, TLS...
            err = f"{type(e).__name__}: {e}"
            time.sleep(0.5)
    return 0, {}, None, err

def get_json(url, **kw):
    st, h, body, err = http_get(url, **kw)
    if body is None:
        return st, None, err
    try:
        return st, json.loads(body.decode("utf-8", "replace")), None
    except Exception as e:
        return st, None, f"invalid JSON: {e}"

# ---------------------------------------------------------------- text utils
TAG = re.compile(r"<[^>]+>")
def clean_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"</(p|div|li|h\d|br)>|<br\s*/?>", "\n", s, flags=re.I)
    s = TAG.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()

def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def job(company, ats, jid, title, url, location, remote=None, desc="", dept="", posted="", extra=None):
    return {"company": company, "ats": ats, "id": str(jid), "title": (title or "").strip(), "url": url,
            "location": (location or "").strip(), "remote_flag": remote, "dept": dept or "", "posted": posted or "",
            "desc": (desc or "")[:20000], **(extra or {})}

# ---------------------------------------------------------------- ATS adapters
# fetch(slug, company) -> (jobs | None, error | None)

def gh_fetch(slug, company):
    st, d, err = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if d is None:
        return None, err or f"HTTP {st}"
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offs = "; ".join(o.get("name", "") for o in j.get("offices", []) if o.get("name"))
        dept = "; ".join(x.get("name", "") for x in j.get("departments", []) if x.get("name"))
        out.append(job(company, "greenhouse", j["id"], j.get("title"), j.get("absolute_url"), loc,
                       None, "", dept, j.get("updated_at", ""),
                       {"offices": offs, "slug": slug}))
    return out, None

def gh_enrich(j):
    """Only for candidates: download the detail (description) of a single posting."""
    st, d, err = get_json(f"https://boards-api.greenhouse.io/v1/boards/{j['slug']}/jobs/{j['id']}")
    if isinstance(d, dict):
        j["desc"] = clean_html(d.get("content", ""))[:20000]

def lever_fetch(slug, company):
    for host in ("api.lever.co", "api.eu.lever.co"):
        st, d, err = get_json(f"https://{host}/v0/postings/{slug}?mode=json")
        if isinstance(d, list):
            out = []
            for j in d:
                c = j.get("categories") or {}
                locs = c.get("allLocations") or ([c["location"]] if c.get("location") else [])
                wt = (j.get("workplaceType") or "").lower()
                out.append(job(company, "lever", j["id"], j.get("text"), j.get("hostedUrl"), "; ".join(locs),
                               True if wt == "remote" else (False if wt == "on-site" else None),
                               j.get("descriptionPlain", "") + "\n" + " ".join(
                                   (l.get("text", "") + " " + clean_html(l.get("content", ""))) for l in j.get("lists", [])),
                               c.get("team", ""), str(j.get("createdAt", "")), {"country": j.get("country", "")}))
            return out, None
    return None, err or f"HTTP {st}"

def ashby_fetch(slug, company):
    st, d, err = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if d is None:
        return None, err or f"HTTP {st}"
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [x.get("location", "") if isinstance(x, dict) else str(x) for x in j.get("secondaryLocations", [])]
        comp = ((j.get("compensation") or {}).get("compensationTierSummary") or
                (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or "")
        wt = (j.get("workplaceType") or "").lower()
        out.append(job(company, "ashby", j.get("id"), j.get("title"), j.get("jobUrl"), "; ".join(x for x in locs if x),
                       True if (j.get("isRemote") or wt == "remote") else None,
                       j.get("descriptionPlain", ""), j.get("department", "") or j.get("team", ""),
                       j.get("publishedAt", ""),
                       {"comp_declared": comp, "workplace": wt,
                        "country": ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")}))
    return out, None

def workable_fetch(slug, company):
    st, d, err = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if d is None:
        return None, err or f"HTTP {st}"
    out = []
    for j in d.get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        out.append(job(company, "workable", j.get("shortcode"), j.get("title"), j.get("url") or j.get("application_url"),
                       loc, True if j.get("telecommuting") else None, clean_html(j.get("description", "")),
                       j.get("department", ""), j.get("published_on", ""), {"country": j.get("country", "")}))
    return out, None

def sr_fetch(slug, company):
    out, off = [], 0
    while True:
        st, d, err = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={off}")
        if d is None:
            return (out or None), err or f"HTTP {st}"
        items = d.get("content", [])
        for j in items:
            l = j.get("location") or {}
            loc = ", ".join(x for x in (l.get("city"), l.get("region"), (l.get("country") or "").upper()) if x)
            out.append(job(company, "smartrecruiters", j.get("id"), j.get("name"),
                           f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", loc,
                           True if l.get("remote") else None, "", (j.get("department") or {}).get("label", ""),
                           j.get("releasedDate", ""), {"country": (l.get("country") or "").upper(),
                                                       "hybrid": bool(l.get("hybrid"))}))
        off += 100
        if off >= d.get("totalFound", 0) or not items:
            break
    return out, None

def recruitee_fetch(slug, company):
    st, d, err = get_json(f"https://{slug}.recruitee.com/api/offers/")
    if d is None:
        return None, err or f"HTTP {st}"
    out = []
    for j in d.get("offers", []):
        loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        out.append(job(company, "recruitee", j.get("id"), j.get("title"), j.get("careers_url"), loc,
                       True if j.get("remote") else None,
                       clean_html((j.get("description") or "") + " " + (j.get("requirements") or "")),
                       j.get("department", ""), j.get("published_at", ""), {"country": j.get("country_code", "")}))
    return out, None

def personio_fetch(slug, company):
    err = None
    for tld in ("de", "com"):
        st, h, body, err = http_get(f"https://{slug}.jobs.personio.{tld}/xml")
        if body:
            try:
                root = ET.fromstring(body)
            except Exception as e:
                err = f"XML: {e}"; continue
            out = []
            for p in root.iter("position"):
                g = lambda t: (p.findtext(t) or "").strip()
                desc = " ".join(clean_html(x.findtext("value") or "") for x in p.iter("jobDescription"))
                out.append(job(company, "personio", g("id"), g("name"), f"https://{slug}.jobs.personio.{tld}/job/{g('id')}",
                               g("office"), None, desc, g("department"), g("createdAt"),
                               {"employment": g("employmentType"), "schedule": g("schedule")}))
            return out, None
    return None, err

def teamtailor_fetch(slug, company):
    st, h, body, err = http_get(f"https://{slug}.teamtailor.com/jobs.rss")
    if not body:
        return None, err or f"HTTP {st}"
    try:
        root = ET.fromstring(body)
    except Exception as e:
        return None, f"XML: {e}"
    out = []
    for it in root.iter("item"):
        def g(tag):
            for el in it:
                if el.tag.split("}")[-1] == tag:
                    return (el.text or "").strip()
            return ""
        locs = []
        for el in it.iter():
            if el.tag.split("}")[-1] == "location":
                c = ", ".join((x.text or "").strip() for x in el if x.text and x.tag.split("}")[-1] in ("city", "country", "name"))
                if c: locs.append(c)
        rem = g("remoteStatus").lower()
        out.append(job(company, "teamtailor", g("guid") or g("link"), g("title"), g("link"), "; ".join(locs),
                       True if rem in ("fully", "remote", "full") else None, clean_html(g("description")),
                       g("department"), g("pubDate"), {"remote_status": rem}))
    return out, None

ADAPTERS = {"greenhouse": gh_fetch, "lever": lever_fetch, "ashby": ashby_fetch, "workable": workable_fetch,
            "smartrecruiters": sr_fetch, "recruitee": recruitee_fetch, "personio": personio_fetch,
            "teamtailor": teamtailor_fetch}
def probe(ats, slug):
    """Does the board exist? Returns (ok, n_jobs, board_name|None). SmartRecruiters needs totalFound>0."""
    if ats == "greenhouse":
        st, d, _ = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if isinstance(d, dict) and d.get("name"):
            st2, d2, _ = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            return True, len((d2 or {}).get("jobs", [])) if d2 else None, d["name"]
        return False, None, None
    if ats == "smartrecruiters":
        st, d, _ = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
        n = (d or {}).get("totalFound", 0) if isinstance(d, dict) else 0
        return n > 0, n, None
    jobs, err = ADAPTERS[ats](slug, slug)
    if jobs is None:
        return False, None, None
    return len(jobs) > 0, len(jobs), None  # an empty board = non-existent/inactive slug (Ashby/Workable answer 200 even for invented slugs)

# ---------------------------------------------------------------- slug verification / company mapping
ATS_URL_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_\-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([a-z0-9_\-\.]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-\.%]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9_\-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)"),
    ("recruitee", r"([a-z0-9\-]+)\.recruitee\.com"),
    ("personio", r"([a-z0-9\-]+)\.jobs\.personio\.(?:de|com)"),
    ("teamtailor", r"([a-z0-9\-]+)\.teamtailor\.com"),
]
NOT_SLUG = {"embed", "api", "www", "jobs", "careers", "apply", "static", "boards"}
STRIP = re.compile(r"\b(careers?|jobs?|group|spa|srl|tech|technology|vacancies|page|worldwide|global|inc|ltd|gmbh|ag|se|sa|europe)\b", re.I)

def find_ats_in_text(text):
    """Find ATS board slugs in an HTML page / URL list (e.g. a company careers page)."""
    found = []
    for ats, pat in ATS_URL_PATTERNS:
        for m in re.finditer(pat, text or "", flags=re.I):
            s = m.group(1)
            if s.lower() in NOT_SLUG:
                continue
            found.append((ats, s.rstrip(".")))
    return found

def name_candidates(name, url=""):
    """Guess a few slugs from the company name and the careers host."""
    base = STRIP.sub(" ", re.sub(r"\(.*?\)", " ", name))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", base.lower()) if w]
    cands = ["".join(words), "-".join(words)] if words else []
    if url:
        host = urllib.parse.urlparse(url).netloc.lower()
        cands += [p for p in host.split(".") if p not in ("www", "careers", "jobs", "career", "com", "io", "net", "org", "eu", "co", "uk", "ai")][:1]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen and len(c) >= 3:
            seen.add(c); out.append(c)
    return out[:3]

def name_token(name):
    base = STRIP.sub(" ", re.sub(r"\(.*?\)", " ", name))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", base.lower()) if w]
    return "".join(words[:2]) if words else norm(name)

def verify_hit(ats, slug, name):
    """A GUESSED slug is 'verified' only if the company name shows up in the ATS data (board name, or >=80% of
    >=10 sampled postings citing it). This avoids homonyms (a slug that exists but belongs to another company).
    Known homonyms can be listed in config['deny_slugs'] as [ats, slug] pairs."""
    deny = {tuple(x) for x in config.get().get("deny_slugs", [])}
    tok = name_token(name)
    short = tok[:6] if len(tok) > 6 else tok
    if (ats, slug) in deny:
        return False, "known homonym (denied in config)"
    def exact(nm):
        n = norm(nm)
        return n.startswith(short) and len(n) <= len(short) + 14
    try:
        if ats == "smartrecruiters":
            st, d, _ = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=3")
            nm = (((d or {}).get("content") or [{}])[0].get("company") or {}).get("name", "")
            return (exact(nm) if nm else False), nm
        if ats == "workable":
            st, d, _ = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            nm = (d or {}).get("name", "")
            return exact(nm), nm
        if ats == "greenhouse":
            st, d, _ = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            nm = (d or {}).get("name", "")
            return exact(nm), nm
        jobs, err = ADAPTERS[ats](slug, name)
        if not jobs:
            return False, ""
        sample = jobs[:25]
        hits = sum(1 for j in sample if short in norm(j["title"] + j["desc"] + j["url"] + j["location"]))
        return (len(sample) >= 10 and hits >= 0.8 * len(sample)), f"{hits}/{len(sample)} postings cite '{short}'"
    except Exception as e:
        return False, f"error: {e}"

def map_company(c):
    """c = {"name":..., "careers_url": optional, "ats": optional list of {"ats","slug"}}.
    Fills c["ats"] with [{"ats","slug","evidence","verified"}]. Evidence order: slugs found on the careers page (strong),
    then guessed slugs probed across all ATS (verified with verify_hit)."""
    hits, seen = [], set()
    for h in c.get("ats", []):
        key = (h["ats"], h["slug"].lower())
        if key not in seen:
            seen.add(key); hits.append({**h, "evidence": h.get("evidence", "manual"), "verified": bool(h.get("verified", True))})
    url = c.get("careers_url") or ""
    if url and not hits:
        st, h, body, err = http_get(url, timeout=20, max_bytes=2_000_000, retries=1)
        for ats, slug in find_ats_in_text((body or b"").decode("utf-8", "replace") + " " + url):
            key = (ats, slug.lower())
            if key in seen: continue
            ok, n, nm = probe(ats, slug)
            if ok:
                seen.add(key); hits.append({"ats": ats, "slug": slug, "evidence": "careers-page", "verified": True, "jobs": n})
    if not hits:
        for slug in name_candidates(c["name"], url):
            for ats in ADAPTERS:
                ok, n, nm = probe(ats, slug)
                if not ok: continue
                v, note = verify_hit(ats, slug, c["name"])
                hits.append({"ats": ats, "slug": slug, "evidence": "guessed-slug", "verified": bool(v), "verify_note": note, "jobs": n})
    c["ats"] = hits
    return c

def build_map(companies):
    """Map a list of companies in parallel (8 workers)."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(map_company, companies))

# ---------------------------------------------------------------- liveness
def liveness(j):
    """Is the posting page still up? (the posting was already in the API list at scan time.)"""
    st, h, body, err = http_get(j["url"], timeout=15, max_bytes=300_000, retries=1)
    if st in (404, 410):
        return "closed_http_%d" % st
    if st == 0:
        return "unreachable"
    txt = (body or b"").decode("utf-8", "replace").lower()
    if re.search(r"no longer (accepting|available)|position (has been )?(filled|closed)|job (not found|is no longer)|posting (has been )?closed|non è più disponibile", txt):
        return "closed_text"
    return "open_http_%d" % st
