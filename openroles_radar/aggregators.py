"""Level B: job aggregators with PUBLIC APIs/feeds, no login, read-only.
Terms of use: the source name and the link to the original posting are kept on every row (attribution is required by
RemoteOK, Jobicy, Remotive, Arbeitnow). Few calls per source (Remotive asks for <=4/day; this uses 3 categories per run
by default). Structure inspired by the providers idea of career-ops (https://github.com/career-ops-hq/career-ops, MIT);
code written from scratch.
"""
import html, json, re, urllib.parse
import xml.etree.ElementTree as ET
from . import config
from .ats import http_get, get_json, clean_html, job

def _xml(body):
    t = body.decode("utf-8", "replace")
    t = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", t)
    return ET.fromstring(t)

def _items(url):
    st, h, body, err = http_get(url)
    if not body:
        return None, err or f"HTTP {st}"
    try:
        root = _xml(body)
    except Exception as e:
        return None, f"XML: {e}"
    return root.findall("channel/item"), None

def _tx(it, tag):
    for el in it:
        if el.tag.split("}")[-1] == tag:
            return (el.text or "").strip()
    return ""

def wwr():
    out, errs = [], []
    for cat in config.get()["aggregators"]["wwr_categories"]:
        items, err = _items(f"https://weworkremotely.com/categories/{cat}.rss")
        if items is None:
            errs.append(f"{cat}: {err}"); continue
        for it in items:
            title = _tx(it, "title")
            comp, _, role = title.partition(": ")
            region = ", ".join(x for x in (_tx(it, "region"), _tx(it, "country"), _tx(it, "state")) if x)
            out.append(job(comp or "?", "agg:weworkremotely", _tx(it, "guid") or _tx(it, "link"), role or title, _tx(it, "link"),
                           region or "Remote", True, clean_html(_tx(it, "description")), cat, _tx(it, "pubDate")))
    return out, "; ".join(errs)

def remoteok():
    st, d, err = get_json("https://remoteok.com/api")
    if not isinstance(d, list):
        return [], err or f"HTTP {st}"
    out = [job(j.get("company"), "agg:remoteok", j.get("id"), j.get("position"), j.get("url") or j.get("apply_url"),
               j.get("location") or "Remote", True, clean_html(j.get("description", "")), ",".join(j.get("tags", [])[:5]), j.get("date", ""),
               {"comp_declared": (f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else "")})
           for j in d if isinstance(j, dict) and j.get("position")]
    return out, ""

def remotive():
    out, errs, seen = [], [], set()
    for cat in config.get()["aggregators"]["remotive_categories"]:
        st, d, err = get_json(f"https://remotive.com/api/remote-jobs?category={cat}")
        if not d:
            errs.append(f"{cat}: {err or st}"); continue
        for j in d.get("jobs", []):
            if j["id"] in seen: continue
            seen.add(j["id"])
            out.append(job(j.get("company_name"), "agg:remotive", j["id"], j.get("title"), j.get("url"),
                           j.get("candidate_required_location") or "", True, clean_html(j.get("description", "")),
                           j.get("category", ""), j.get("publication_date", ""), {"comp_declared": j.get("salary") or ""}))
    return out, "; ".join(errs)

def arbeitnow(pages=4):
    out, errs = [], []
    for p in range(1, pages + 1):
        st, d, err = get_json(f"https://www.arbeitnow.com/api/job-board-api?page={p}")
        if not d:
            errs.append(f"p{p}: {err or st}"); break
        for j in d.get("data", []):
            out.append(job(j.get("company_name"), "agg:arbeitnow", j.get("slug"), j.get("title"), j.get("url"),
                           (j.get("location") or "") + (" ; Remote" if j.get("remote") else ""), True if j.get("remote") else None,
                           clean_html(j.get("description", "")), ",".join(j.get("tags", [])[:4]), str(j.get("created_at", ""))))
        if not d.get("links", {}).get("next"):
            break
    return out, "; ".join(errs)

def himalayas():
    hcfg = config.get()["aggregators"]["himalayas"]
    qs, country = hcfg["queries"], hcfg.get("country", "")
    out, errs, seen = [], [], set()
    for q in qs:
        for page in (1, 2):
            st, d, err = get_json("https://himalayas.app/jobs/api/search?" + urllib.parse.urlencode({"q": q, **({"country": country} if country else {}), "page": page}))
            if not d:
                errs.append(f"{q}: {err or st}"); break
            for j in d.get("jobs", []):
                if j["guid"] in seen: continue
                seen.add(j["guid"])
                restr = j.get("locationRestrictions") or []
                loc = "; ".join(restr) if len(restr) <= 12 else "Europe (" + str(len(restr)) + " countries" + ((", incl. " if country in restr else ", NOT incl. ") + country if country else "") + ")"
                comp = ""
                if j.get("minSalary"):
                    comp = f"{j.get('currency','')} {j['minSalary']}-{j.get('maxSalary')} /{j.get('salaryPeriod','')}"
                out.append(job(j.get("companyName"), "agg:himalayas", j["guid"], j.get("title"), (j.get("applicationLink") or j["guid"]).split("?utm_source")[0], loc or "Worldwide",
                               True, clean_html(j.get("description", "")), ",".join(j.get("categories", [])[:3]), j.get("pubDate", ""),
                               {"comp_declared": comp, "restrictions": restr, "seniority": ",".join(j.get("seniority", []) or [])}))
            if len(d.get("jobs", [])) < 20:
                break
    return out, "; ".join(errs[:3])

def jobicy():
    out, errs, seen = [], [], set()
    for geo in config.get()["aggregators"]["jobicy_geos"]:
        st, d, err = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=100&geo={geo}")
        if not d:
            errs.append(f"{geo}: {err or st}"); continue
        for j in d.get("jobs", []):
            if j["id"] in seen: continue
            seen.add(j["id"])
            geo_s = j.get("jobGeo", "")
            if isinstance(geo_s, list): geo_s = ", ".join(geo_s)
            comp = f"{j.get('salaryCurrency','')} {j['salaryMin']}-{j.get('salaryMax')} /{j.get('salaryPeriod','')}" if j.get("salaryMin") else ""
            out.append(job(j.get("companyName"), "agg:jobicy", j["id"], j.get("jobTitle"), j.get("url"), geo_s, True,
                           clean_html(j.get("jobDescription", "")), str(j.get("jobIndustry", "")), j.get("pubDate", ""), {"comp_declared": comp}))
    return out, "; ".join(errs)

def landingjobs(pages=6):
    out, errs = [], []
    for p in range(pages):
        st, d, err = get_json(f"https://landing.jobs/api/v1/jobs?limit=50&offset={p*50}")
        if not isinstance(d, list):
            errs.append(f"offset {p*50}: {err or st}"); break
        for j in d:
            locs = "; ".join(f"{l.get('city','')} {l.get('country_code','')}".strip() for l in j.get("locations", []))
            desc = clean_html((j.get("role_description") or "") + " " + (j.get("main_requirements") or "") + " " + (j.get("nice_to_have") or ""))
            comp = f"{j.get('currency_code','')} {j['gross_salary_low']}-{j.get('gross_salary_high')} gross" if j.get("gross_salary_low") else ""
            out.append(job("Landing.jobs/" + str(j.get("id")), "agg:landingjobs", j["id"], j.get("title"), j.get("url"), locs,
                           True if j.get("remote") else None, desc, ",".join(j.get("tags", [])[:4]), j.get("published_at", ""),
                           {"comp_declared": comp, "company_unknown": True}))
        if len(d) < 50:
            break
    return out, "; ".join(errs)

def workingnomads():
    st, d, err = get_json("https://www.workingnomads.com/api/exposed_jobs/")
    if not isinstance(d, list):
        return [], err or f"HTTP {st}"
    return [job(j.get("company_name"), "agg:workingnomads", j.get("url"), j.get("title"), j.get("url"), j.get("location") or "",
                True, clean_html(j.get("description", "")), j.get("category_name", ""), j.get("pub_date", "")) for j in d], ""

def jobspresso():
    items, err = _items("https://jobspresso.co/feed/?post_type=job_listing")
    if items is None:
        return [], err
    out = []
    for it in items:
        title = _tx(it, "title")
        out.append(job(_tx(it, "creator") or "?", "agg:jobspresso", _tx(it, "guid"), title, _tx(it, "link"), "Remote", True,
                       clean_html(_tx(it, "encoded") or _tx(it, "description")), "", _tx(it, "pubDate")))
    return out, ""

def nodesk():
    items, err = _items("https://nodesk.co/remote-jobs/index.xml")
    if items is None:
        return [], err
    out = []
    for it in items:
        title = _tx(it, "title")
        out.append(job("?", "agg:nodesk", _tx(it, "guid") or _tx(it, "link"), title, _tx(it, "link"), "Remote", True,
                       clean_html(_tx(it, "description")), "", _tx(it, "pubDate")))
    return out, ""

AGGREGATORS = {"We Work Remotely": wwr, "RemoteOK": remoteok, "Remotive": remotive, "Arbeitnow": arbeitnow,
               "Himalayas": himalayas, "Jobicy": jobicy, "Landing.jobs": landingjobs, "Working Nomads": workingnomads,
               "Jobspresso": jobspresso, "NoDesk": nodesk}
def enabled():
    """Aggregators enabled in config['aggregators']['enabled'] (order preserved)."""
    names = config.get()["aggregators"]["enabled"]
    return {n: AGGREGATORS[n] for n in names if n in AGGREGATORS}

# Deliberately NOT supported: sites that need login/keys or forbid scraping (LinkedIn, Indeed, ...).
