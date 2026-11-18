"""Filters on structured location, full-text location, pay, role families and the internal 1-5 score.

Everything personal (home location, role families, skills, gaps, salary floor) comes from the JSON config.
The score only ORDERS candidates; it never discards anything by itself. Ambiguous cases are marked
`needs_check` instead of being dropped.
"""
import re
from . import config

# ================================================================ compiled config (lazy)
_cache = {"cfg": None}

def _C():
    cfg = config.get()
    if _cache["cfg"] is cfg:
        return _cache
    loc = cfg["location"]; roles = cfg["roles"]
    _cache.update(cfg=cfg, loc=loc)
    _cache["home_country"] = [t.lower() for t in loc["home_country_terms"]]
    _cache["home_cities"] = [t.lower() for t in loc["home_country_cities"]]
    _cache["home_region"] = [t.lower() for t in loc["home_region_terms"]]
    _cache["fams"] = roles["families"]
    _cache["primary"] = {f["name"] for f in roles["families"] if f.get("primary")}
    _cache["title_pats"] = {f["name"]: [(re.compile(rx, re.I), w) for rx, w in f.get("title_patterns", [])] for f in roles["families"]}
    for rx, w, fam in roles.get("extra_title_patterns", []):
        _cache["title_pats"].setdefault(fam, []).append((re.compile(rx, re.I), w))
    _cache["text_pats"] = {f["name"]: re.compile(f["text_pattern"]) for f in roles["families"] if f.get("text_pattern")}
    _cache["hard_neg"] = re.compile(roles["hard_negative_regex"], re.I) if roles.get("hard_negative_regex") else None
    _cache["dev_neg"] = re.compile(roles["dev_negative_regex"], re.I) if roles.get("dev_negative_regex") else None
    _cache["skills"] = [(re.compile(rx), lab) for rx, lab in cfg["profile"]["skills"]]
    _cache["gaps"] = [(re.compile(g[0]), g[1], len(g) > 2 and g[2] == "weak") for g in cfg["profile"]["gaps"]]
    return _cache

def _has(toks, s):
    """Token match with word boundaries; a token ending with '*' is a prefix/stem match."""
    for t in toks:
        t = t.strip()
        stem = t.endswith("*"); t = t.rstrip("*")
        if t and re.search(r"(?<![a-z])" + re.escape(t) + ("" if stem else r"(?![a-z])"), s):
            return True
    return False

# ================================================================ structured location
US_STATES = r"\b(CA|NY|TX|WA|MA|IL|CO|GA|FL|NC|VA|OR|PA|OH|AZ|NJ|MI|MN|UT|DC)\b"

def loc_class(j):
    """Returns (class, reason). OK classes: home_region, home_country_remote, remote_emea_eu, remote_worldwide,
    remote_unspecified, home_country_only, home_country_onsite_other. Everything else starts with 'excluded_'."""
    C = _C(); loc = C["loc"]
    raw = j["location"]
    L = " " + raw.lower().replace("/", " ; ") + " "
    desc = (j.get("desc") or "").lower()
    remote = bool(j.get("remote_flag")) or any(k in L for k in ("remote", "home based", "home-based", "distributed", "remoto", "anywhere", "work from home", "home office"))
    in_region = _has(C["home_region"], L)
    in_country = _has(C["home_country"] + C["home_cities"], L)
    broad = _has(loc["broad_terms"], L) or bool(re.search(r"\beu\b", L))
    non_home = _has(loc["non_home_terms"], L) or bool(re.search(US_STATES, raw))
    eu_country = [c for c in loc["european_countries"] if re.search(r"\b" + re.escape(c) + r"\b", L) and c not in C["home_country"]]
    if in_region: return "home_region", "home region (location/remote)"
    if remote and in_country: return "home_country_remote", "remote including the home country"
    if remote and broad: return "remote_emea_eu", "remote EMEA/Europe"
    if remote and ("worldwide" in L or "anywhere" in L or "global" in L): return "remote_worldwide", "remote worldwide"
    left = re.sub(r"\b(remote|remoto|distributed|hybrid|flex|office|in|based|home|onsite|on-site|anywhere|work|from|us)\b", " ", L)
    left = re.sub(r"[^a-z]+", " ", left).strip()
    if remote and not left:
        if re.search(r"(must|need to) (be|reside|live)[^.]{0,40}(united states|u\.s\.|us\b|canada|uk\b)|authorized to work in the (united states|u\.s\.)|us[- ]based", desc) or re.search(r"\bus\b", L):
            return "excluded_remote_us_only_desc", "US/UK/CA only (location or description)"
        return "remote_unspecified", "remote without country (check allowed countries)"
    if remote and not in_country and not broad:
        return "excluded_remote_incompatible_region", "remote limited to: " + raw[:60]
    rest = L.replace(";", " ")
    for t in C["home_country"]: rest = rest.replace(t, " ")
    if in_country and C["home_country"] and not _has(C["home_cities"], L) and not re.sub(r"[^a-z]+", " ", rest).strip():
        return "home_country_only", "country only, no city: check whether remote"
    if in_country: return "home_country_onsite_other", "home country, on-site outside your region: evaluate (no relocation)"
    if non_home or eu_country: return "excluded_foreign_onsite", "foreign on-site without remote"
    if not raw.strip(): return "remote_unspecified", "location not stated (check)"
    return "excluded_other_location", "location: " + raw[:60]

# ================================================================ location from the full text
ALLOW = [r"\bemea\b", r"\beurope(an)?\b", r"\beu\b", r"\beea\b", r"\bworldwide\b", r"\bglobal(ly)?\b", r"\banywhere\b",
         r"\bany (country|location)\b", r"\bcet\b", r"\butc ?[+\-]? ?[0-3]\b", r"\bsmart ?working\b"]
BLOCK = [r"\b(united states|u\.s\.a?\.?|usa)\b[^.]{0,40}\b(only|based|resident|residents|citizens?)\b",
         r"\b(must|need to|required to) (be|reside|live|work)[^.]{0,50}\b(united states|u\.s\.|us|canada|uk|united kingdom|india|brazil|australia)\b",
         r"\bauthori[sz]ed to work in the (united states|u\.s\.|us|uk|united kingdom|canada)\b",
         r"\bus[- ]based\b", r"\bus citizens?\b", r"\bsecurity clearance\b", r"\bgreen card\b", r"\bwork permit for (the )?(us|uk|canada)\b",
         r"\bremote[^.]{0,20}\b(us|usa|united states|canada|latam|apac|india)\b[^.]{0,10}\bonly\b",
         r"\b(americas|north america|latam|apac) only\b"]
HYBRID_RX = re.compile(r"\b(hybrid|days? (per|a) week in (the )?office|in[- ]office|on[- ]?site)\b", re.I)
REMOTE_RX = re.compile(r"\b(fully remote|100% remote|remote[- ]first|remote|smart ?working|full remote|work from home|telework)\b", re.I)
RELOC_RX = re.compile(r"\b(relocat(e|ion) (is )?(required|mandatory|expected)|must relocate|willing to relocate)\b", re.I)

def text_location(j, base_class):
    """Returns (decision, allowed_countries_str, reason, needs_check). decision: 'ok' | 'exclude'.
    Ambiguous cases are 'ok' + needs_check, never silently dropped."""
    C = _C(); loc_cfg = C["loc"]
    d = j.get("desc") or ""; low = d.lower(); loc = j.get("location", "")
    allowed = sorted({m.group(0).lower() for rx in ALLOW for m in re.finditer(rx, low)})
    ctry_rx = re.compile(r"\b(" + "|".join(re.escape(c) for c in loc_cfg["european_countries"] if c not in C["home_country"]) + r")\b", re.I)
    countries = sorted({m.group(0).lower() for m in ctry_rx.finditer(d[:6000])})
    block = [m.group(0) for rx in BLOCK for m in re.finditer(rx, low)]
    remote_txt = bool(REMOTE_RX.search(d)) or bool(j.get("remote_flag"))
    hybrid = bool(HYBRID_RX.search(d)) and "fully remote" not in low
    strong = base_class in ("home_region", "home_country_remote", "remote_emea_eu", "remote_worldwide")
    home_in_text = any(t in low for t in C["home_country"])
    paesi = []
    if home_in_text: paesi.append("home country mentioned")
    if re.search(r"\b(emea|europe|eu)\b", low): paesi.append("Europe/EMEA mentioned")
    if re.search(r"\b(worldwide|anywhere|global)\b", low): paesi.append("worldwide/anywhere mentioned")
    if countries: paesi.append("countries mentioned: " + ", ".join(countries[:6]))
    paesi_s = "; ".join(paesi) if paesi else "not stated in the text"
    if C["home_region"] and _has(C["home_region"], (loc + " " + low[:800]).lower()):
        return "ok", paesi_s, "home region in location or text", hybrid
    if block and not strong:
        return "exclude", paesi_s, "geographic restriction in text: " + block[0][:70], False
    if block and strong:
        return "ok", paesi_s, "structured location compatible but text has a restriction: " + block[0][:70], True
    if RELOC_RX.search(d) and not strong:
        return "exclude", paesi_s, "requires relocation", False
    if base_class in ("home_country_onsite_other", "home_country_only"):
        if remote_txt and not hybrid:
            return "ok", paesi_s, "home country with remote in the text", True
        if remote_txt and hybrid:
            return "ok", paesi_s, "home country, remote/hybrid: check on-site days", True
        return "exclude", paesi_s, "home country outside your region with no remote in the text", False
    if base_class == "remote_unspecified":
        if allowed and not countries:
            return "ok", paesi_s, "remote unspecified, text without country limits", True
        if countries and not home_in_text and not re.search(r"\b(emea|europe|eu|worldwide|anywhere)\b", low):
            return "exclude", paesi_s, "remote but only countries other than home in the text", False
        return "ok", paesi_s, "remote without country: check eligibility", True
    if strong:
        return "ok", paesi_s, "structured location compatible (" + base_class + ")", base_class == "remote_emea_eu" and hybrid
    return "ok", paesi_s, base_class, True

# ================================================================ pay
NUM = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s?([kK])?"
_CUR = r"(?:€|eur|usd|\$|gbp|£|chf|ron|pln|sek|dkk|czk)"
PAY_RX = re.compile("(" + _CUR + r")\s?" + NUM + r"\s?(?:-|–|—|to|a|and)\s?" + _CUR + r"?\s?" + NUM + r"(?:\s?" + _CUR + r")?"
                    r"(?:\s?(?:/|per|a|l')?\s?(year|yr|annum|anno|annual|month|mese|hour|ora|day|giorno|ral|gross|lordo|p\.a\.))?", re.I)
RAL_RX = re.compile(r"\b(?:RAL|gross annual salary)\b[^.\n]{0,40}?(\d{2}(?:[.,]\d{3})?)\s?(k)?(?:\s?(?:-|–|a|e|to)\s?(\d{2}(?:[.,]\d{3})?)\s?(k)?)?", re.I)

def _n(s, k):
    s = s.replace(" ", "")
    v = float(s.replace(".", "").replace(",", "")) if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s) else float(s.replace(",", "."))
    return v * 1000 if k else v

def extract_pay(j):
    """Returns (declared_text, max_annual_eur | None, kind). Hourly/daily rates are not treated as annual pay."""
    fx = {k.upper(): v for k, v in config.get()["salary"]["fx_to_eur"].items()}
    txt = " ".join([j.get("comp_declared") or "", j.get("desc") or ""])
    m = RAL_RX.search(txt)
    if m:
        lo = _n(m.group(1), m.group(2)); hi = _n(m.group(3), m.group(4)) if m.group(3) else lo
        lo = lo * 1000 if lo < 1000 else lo; hi = hi * 1000 if hi < 1000 else hi
        return f"RAL {int(lo)}" + (f"-{int(hi)}" if hi != lo else "") + " EUR (declared)", max(lo, hi), "ral"
    best = None
    for m in PAY_RX.finditer(txt):
        cur = m.group(1).lower().replace("eur", "€").replace("usd", "$").replace("gbp", "£").upper()
        try:
            a = _n(m.group(2), m.group(3)); b = _n(m.group(4), m.group(5))
        except Exception:
            continue
        per = (m.group(6) or "").lower(); hi = max(a, b)
        if per in ("hour", "ora", "day", "giorno"): continue
        if per in ("month", "mese"): hi *= 12
        elif hi < 1000 and not m.group(3) and not m.group(5): continue
        if hi < 10000: continue
        rate = fx.get(cur)
        if rate is None: continue
        eur = hi * rate
        if best is None or eur > best[1]: best = (m.group(0).strip()[:60], eur, "range")
    if best:
        return best[0] + f" (~{int(best[1])} EUR/year max)", best[1], best[2]
    return "", None, ""

def pay_below_threshold(max_eur):
    s = config.get()["salary"]
    return bool(s.get("employee", True)) and max_eur is not None and max_eur < s["min_annual_eur"]

# ================================================================ role families (title + text)
def title_families(title):
    C = _C(); t = title.lower(); out = {}
    for fam, pats in C["title_pats"].items():
        for rx, w in pats:
            if rx.search(t): out[fam] = max(out.get(fam, 0), w)
    return out

def hard_negative(title):
    C = _C()
    return bool(C["hard_neg"] and C["hard_neg"].search(title))

def text_role(j):
    """Returns (passes, families[], details{fam: (title_weight, n_text_hits)}, reason)."""
    C = _C(); t = j["title"]; d = (j.get("desc") or "")[:12000].lower()
    if hard_negative(t):
        return False, [], {}, "title not relevant (junior/sales/recruiting/legal/design...)"
    tf = title_families(t); det = {}
    for fam, rx in C["text_pats"].items():
        hits = {m.group(0) for m in rx.finditer(d)}
        det[fam] = (tf.get(fam, 0), len(hits))
    dev = bool(C["dev_neg"] and C["dev_neg"].search(t))
    fams = []
    for fam, (tw, nd) in det.items():
        score = tw * 2 + min(nd, 5)
        if dev: score -= 3
        if (tw >= 3 and nd >= 1) or (tw >= 2 and nd >= 2) or (nd >= 5 and fam in C["primary"] and not dev):
            fams.append((score, fam))
    if not fams:
        return False, [], det, "no relevant family in title or text"
    fams.sort(reverse=True)
    return True, [f for _, f in fams[:3]], det, "families: " + ", ".join(f for _, f in fams[:3])

# ================================================================ internal score 1-5
LOC_SCORE = {"home_region": 5, "home_country_remote": 5, "remote_worldwide": 4, "remote_emea_eu": 4, "remote_unspecified": 2,
             "home_country_only": 2, "home_country_onsite_other": 2}

def score_dims(j, fams, loc_class_s, needs_check, pay_max, pay_txt=""):
    """Internal score 1-5 over 5 dimensions (skills, location, pay, role, risk). ORDERING ONLY."""
    C = _C(); S = config.get()["scoring"]; w = S["weights"]
    d = ((j.get("desc") or "") + " " + j["title"]).lower()
    bank = [lab for rx, lab in C["skills"] if rx.search(d)]
    gaps = [(lab, weak) for rx, lab, weak in C["gaps"] if rx.search(d)]
    n = len(bank); th = S["skill_thresholds"]
    d1 = 1 + sum(1 for t in th if n > t)
    d2 = LOC_SCORE.get(loc_class_s, 2)
    if needs_check: d2 = max(1, d2 - 1)
    floor = config.get()["salary"]["min_annual_eur"]
    if pay_max is None: d3 = 3
    elif pay_max >= floor: d3 = 5 if pay_max >= floor * 1.2 else 4
    else: d3 = 1
    tf = title_families(j["title"])
    prim_title = any(f in C["primary"] and wt >= 2 for f, wt in tf.items())
    if prim_title: d4 = 5
    elif tf: d4 = 3
    elif fams and fams[0] in C["primary"]: d4 = 3
    else: d4 = 2
    weak = sum(1 for _, wk in gaps if wk)
    d5 = max(1, round(5 - (len(gaps) - weak) - 0.5 * weak))
    total = round((w["skills"] * d1 + w["location"] * d2 + w["pay"] * d3 + w["role"] * d4 + w["risk"] * d5) / sum(w.values()), 1)
    return {"skills": d1, "location": d2, "pay": d3, "role": d4, "risk": d5, "total": total,
            "profile_matches": bank, "risk_list": [g for g, _ in gaps]}
