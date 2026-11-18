"""Incremental scan: public ATS APIs (level A) + public aggregator feeds (level B) -> location/role/pay filters ->
dedup -> liveness check -> internal score -> reports.

INCREMENTAL by design (saves time, requests and model usage): every posting ever seen is stored in `seen.json`
(key + fingerprint + date, even if discarded by the filters). A later run skips seen postings BEFORE downloading details,
filtering or scoring them. Only NEW postings are processed. `--reference` rebuilds the state from scratch.

Read-only on the Internet, no login. Writes only inside `data_dir` (see config).
"""
import csv, datetime, json, os, re, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, ats as A, aggregators as AG, filters as F

TODAY = datetime.date.today().isoformat()

# ---------------------------------------------------------------- keys, fingerprints, handled list
def norm_url(u):
    p = urllib.parse.urlparse((u or "").lower())
    return p.netloc.replace("www.", "") + p.path.rstrip("/")

_CO_STRIP = re.compile(r"\b(careers?|jobs?|inc\.?|ltd\.?|gmbh|s\.?r\.?l\.?|s\.?p\.?a\.?|llc|plc|group)\b")
_REMOTE_CITY = re.compile(r"europe|worldwide|emea|remote|anywhere")

def fingerprint(j):
    """company|title|city fingerprint (company normalized without Careers/Jobs/Inc/...; broad/remote places collapse to 'remote').
    Catches the same posting republished on another source or under another id."""
    comp = _CO_STRIP.sub(" ", j["company"].lower())
    city = re.split(r"[;,(/]", j["location"])[0] if j["location"] else ""
    if _REMOTE_CITY.match(city.strip().lower()):
        city = "remote"
    return A.norm(comp) + "|" + A.norm(j["title"]) + "|" + A.norm(city)

def handled_sets():
    """handled.json = postings you already dealt with by hand (applied, rejected, evaluated): never proposed again.
    List of {"url":..., "id":..., "aliases":[...], "fingerprint":...}. Missing file = empty list."""
    p = config.data_path("handled.json")
    d = _load(p) if os.path.exists(p) else []
    ids, urls, fps = set(), set(), set()
    for e in d:
        if e.get("url"): urls.add(norm_url(e["url"]))
        if e.get("fingerprint"): fps.add(e["fingerprint"])
        for k in ("id", "requisition_id"):
            if e.get(k): ids.add(str(e[k]).lower())
        for a in e.get("aliases") or []:
            ids.add(str(a).lower())
    return ids, urls, fps

# ---------------------------------------------------------------- details
def sr_enrich(j):
    m = re.search(r"smartrecruiters\.com/([^/]+)/(\d+)", j["url"] or "")
    if not m: return
    st, d, err = A.get_json(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
    if isinstance(d, dict):
        secs = (d.get("jobAd") or {}).get("sections") or {}
        j["desc"] = " ".join(A.clean_html(v.get("text", "")) for v in secs.values())[:20000]
        loc = d.get("location") or {}
        if loc.get("fullLocation"): j["location"] = loc["fullLocation"]
        j["remote_flag"] = True if loc.get("remote") else j.get("remote_flag")
        j["employment"] = (d.get("typeOfEmployment") or {}).get("label", "")

def gh_enrich2(j):
    st, d, err = A.get_json(f"https://boards-api.greenhouse.io/v1/boards/{j['slug']}/jobs/{j['id']}")
    if isinstance(d, dict):
        j["desc"] = A.clean_html(d.get("content", ""))[:20000]

def enrich(j):
    """Greenhouse and SmartRecruiters list endpoints have no description: fetch it only for postings that passed the location filter."""
    if j.get("desc"): return
    if j["ats"] == "greenhouse": gh_enrich2(j)
    elif j["ats"] == "smartrecruiters": sr_enrich(j)

# ---------------------------------------------------------------- gathering
def load_companies(path=None):
    p = path or config.get()["companies_file"]
    if not os.path.isabs(p) and not os.path.exists(p):
        p = config.data_path(p)
    return _load(p)["companies"], p

def gather(only_ats=False, only_agg=False, companies_path=None):
    log, jobs = [], []
    comp, _ = load_companies(companies_path)
    targets, seen = [], set()
    for c in comp:
        for h in c.get("ats", []):
            if h.get("verified"):
                k = (h["ats"], h["slug"].lower())
                if k in seen: break
                seen.add(k); targets.append((c, h)); break
    def work(c, h):
        t = time.time(); js, err = A.ADAPTERS[h["ats"]](h["slug"], c["name"]); return c, h, js, err, time.time() - t
    if not only_agg:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for f in as_completed([ex.submit(work, c, h) for c, h in targets]):
                c, h, js, err, dt = f.result()
                log.append({"level": "A", "source": c["name"], "type": h["ats"], "slug": h["slug"],
                            "total_postings": None if js is None else len(js), "errors": err or "", "seconds": round(dt, 1)})
                for j in (js or []):
                    j["_src"] = c["name"]; jobs.append(j)
    if not only_ats:
        for name, fn in AG.enabled().items():
            t = time.time()
            try:
                js, err = fn()
            except Exception as e:
                js, err = [], f"{type(e).__name__}: {e}"
            log.append({"level": "B", "source": name, "type": "aggregator", "slug": "", "total_postings": len(js),
                        "errors": err or "", "seconds": round(time.time() - t, 1)})
            for j in js:
                j["_src"] = name
                if j.get("company_unknown"): j["company"] = "(company not stated) " + name
                jobs.append(j)
    return log, jobs

# ---------------------------------------------------------------- state
def _dump(obj, path, indent=None):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
def load_seen(reference):
    p = config.data_path("seen.json")
    if reference or not os.path.exists(p):
        return {"schema": 1, "reference_round": TODAY, "items": {}, "rounds": []}
    return _load(p)

def save_seen(seen):
    with open(config.data_path("seen.json"), "w", encoding="utf-8") as f: json.dump(seen, f, ensure_ascii=False)

# ---------------------------------------------------------------- main run
def run(only_ats=False, only_agg=False, reference=False, companies_path=None, check_open=True):
    """One incremental round. Returns a dict of totals. Files go to data_dir/output/."""
    kn_ids, kn_urls, kn_fps = handled_sets()
    log, jobs = gather(only_ats, only_agg, companies_path)
    seen = load_seen(reference); items = seen["items"]
    fp_idx = {v["fp"]: k for k, v in items.items() if v.get("fp")}
    fresh, skipped = [], 0
    for j in jobs:
        k = f"{j['ats']}|{j['_src']}|{j['id']}" if j.get("id") else norm_url(j.get("url") or "")
        fp = fingerprint(j); j["_key"], j["_fp"] = k, fp
        hit = k if k in items else fp_idx.get(fp)
        if hit and hit in items and not reference:
            items[hit]["last_seen"] = TODAY; skipped += 1; continue
        fresh.append(j); fp_idx.setdefault(fp, k)
    stage = {}
    n_read = len(jobs); jobs = fresh
    stat = {l["source"]: {"location": 0, "role": 0, "score3": 0, "final": 0} for l in log}
    # stage 1: structured location (ambiguous cases are kept)
    s1 = []
    for j in jobs:
        lc, why = F.loc_class(j)
        if lc.startswith("excluded"): stage[j["_key"]] = "discarded-location"; continue
        if F.hard_negative(j["title"]): stage[j["_key"]] = "discarded-title"; continue
        j["_lc"] = lc; s1.append(j); stat[j["_src"]]["location"] += 1
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(enrich, [j for j in s1 if not j.get("desc")]))
    cands, seen_fp = [], {}
    for j in s1:
        dec, countries, lwhy, check = F.text_location(j, j["_lc"])
        if dec == "exclude": stage[j["_key"]] = "discarded-location-text"; continue
        ok, fams, det, rwhy = F.text_role(j)
        if not ok: stage[j["_key"]] = "discarded-role"; continue
        stat[j["_src"]]["role"] += 1
        pay_txt, pay_max, pay_kind = F.extract_pay(j)
        if F.pay_below_threshold(pay_max) and pay_kind in ("ral", "range"):
            stage[j["_key"]] = "discarded-pay"; continue   # declared pay below the floor: silent discard
        sc = F.score_dims(j, fams, j["_lc"], check, pay_max, pay_txt)
        known = ""; fp = j["_fp"]
        if norm_url(j["url"] or "") in kn_urls: known = "url"
        elif j["id"].lower() in kn_ids: known = "id"
        elif fp in kn_fps: known = "fingerprint-handled"
        elif fp in seen_fp: known = "fingerprint-duplicate-in-round"
        seen_fp.setdefault(fp, j["url"])
        stage[j["_key"]] = "candidate"
        cands.append({"source": j["_src"], "key": j["_key"], "level": "B" if j["ats"].startswith("agg:") else "A", "company": j["company"],
                      "ats": j["ats"], "id": j["id"], "title": j["title"], "url": j["url"], "location": j["location"][:200],
                      "location_class": j["_lc"], "location_reason": lwhy, "countries_in_text": countries, "location_to_check": bool(check),
                      "families": "; ".join(fams), "pay": pay_txt or "PAY UNKNOWN", "pay_max_eur": pay_max, "score": sc["total"],
                      "dims": {k: sc[k] for k in ("skills", "location", "pay", "role", "risk")}, "profile_matches": sc["profile_matches"],
                      "risks": sc["risk_list"], "dedup": known or "new", "fingerprint": fp, "posted": j.get("posted", ""),
                      "desc": j["desc"][:20000], "dept": j.get("dept", ""), "employment": j.get("employment", "")})
        if sc["total"] >= 3: stat[j["_src"]]["score3"] += 1
    # liveness: only new candidates with score >= 3
    if check_open:
        todo = [c for c in cands if c["dedup"] == "new" and c["score"] >= 3]
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(A.liveness, c): c for c in todo}
            for f in as_completed(futs): futs[f]["open_check"] = f.result()
    for c in cands: c.setdefault("open_check", "n/a")
    for c in cands:
        if c["dedup"] == "new": stat[c["source"]]["final"] += 1
    for l in log:
        l.update({"after_location": stat[l["source"]]["location"], "after_role": stat[l["source"]]["role"],
                  "score3plus": stat[l["source"]]["score3"], "new_candidates": stat[l["source"]]["final"]})
    cands.sort(key=lambda c: (-int(c["dedup"] == "new"), -c["score"], c["company"]))
    out = config.data_path("output"); os.makedirs(out, exist_ok=True)
    _dump(cands, os.path.join(out, f"candidates-{TODAY}.json"), indent=1)
    cols = ["score", "company", "title", "location", "location_class", "location_to_check", "countries_in_text", "families", "pay",
            "dedup", "open_check", "source", "level", "url", "id", "location_reason", "risks"]
    with open(os.path.join(out, f"candidates-{TODAY}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for c in cands: w.writerow({**c, "risks": " | ".join(c["risks"])})
    _dump(log, os.path.join(out, f"scan-log-{TODAY}.json"), indent=1)
    # update seen.json: EVERY new posting of this round (even discarded), with date
    for j in jobs:
        items[j["_key"]] = {"first_seen": TODAY, "last_seen": TODAY, "src": j["_src"], "company": j["company"], "title": j["title"][:120],
                            "fp": j["_fp"], "stage": stage.get(j["_key"], "discarded-location")}
    for c in cands: items[c["key"]]["score"] = c["score"]
    seen["rounds"].append({"date": TODAY, "reference": bool(reference), "postings_read": n_read, "already_seen_skipped": skipped,
                           "new_processed": len(jobs), "candidates": len(cands)})
    save_seen(seen)
    tot = {"already_seen_skipped": skipped, "new_processed": len(jobs), "sources_A": sum(1 for l in log if l["level"] == "A"),
           "sources_B": sum(1 for l in log if l["level"] == "B"), "postings_read": sum(l["total_postings"] or 0 for l in log),
           "after_location": len(s1), "after_role": len(cands), "new": sum(1 for c in cands if c["dedup"] == "new"),
           "score3plus_new": sum(1 for c in cands if c["dedup"] == "new" and c["score"] >= 3), "errors": [l["source"] + ": " + l["errors"] for l in log if l["errors"]]}
    _dump(tot, os.path.join(out, f"totals-{TODAY}.json"), indent=1)
    return tot
