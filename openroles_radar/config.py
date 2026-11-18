"""Configuration loading. JSON only (Python stdlib).

Lookup order: explicit path -> $OPENROLES_CONFIG -> ./config.json -> bundled config.example.json.
User values are deep-merged over DEFAULTS, so a config only needs the keys you want to change.
"""
import copy, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "data_dir": "./data",
    "user_agent": "openroles-radar/0.1 (+https://github.com/stefanogensabella-cyber/openroles-radar; personal job search, read-only, public APIs)",
    "min_interval_seconds": 0.25,          # minimum delay between two requests to the same host
    "companies_file": "companies.json",    # relative to data_dir unless absolute
    "deny_slugs": [],                      # [["greenhouse","some-homonym"], ...] known slug homonyms to never use
    "location": {
        "home_country_terms": [],          # e.g. ["germany", "deutschland"]
        "home_country_cities": [],         # e.g. ["berlin", "munich", "hamburg"]
        "home_region_terms": [],           # your own region/cities: strongest location class
        "broad_terms": ["emea", "europe", "european", "eu ", "(eu)", "eu-", "eea", "worldwide", "global", "anywhere",
                        "world wide", "international", "any location", "everywhere"],
        "non_home_terms": ["united states", "usa", "u.s.", "us only", "us-", "(us)", "canada", "americas", "latam", "latin america",
                           "brazil", "mexico", "india", "apac", "asia", "australia", "singapore", "japan", "china", "israel", "uae",
                           "dubai", "south africa", "new zealand", "argentina", "colombia", "north america"],
        "european_countries": ["austria", "belgium", "bulgaria", "croatia", "cyprus", "czech", "czechia", "denmark", "estonia",
                               "finland", "france", "germany", "greece", "hungary", "ireland", "latvia", "lithuania", "luxembourg",
                               "malta", "netherlands", "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
                               "norway", "switzerland", "united kingdom", "uk", "london", "italy"],
        "remote_ok_hybrid_needs_check": True,
    },
    "salary": {
        "min_annual_eur": 50000,           # employee roles with a declared max below this are silently dropped
        "employee": True,
        "fx_to_eur": {"EUR": 1.0, "€": 1.0, "USD": 0.86, "$": 0.86, "GBP": 1.15, "£": 1.15, "CHF": 1.07,
                      "RON": 0.20, "PLN": 0.23, "SEK": 0.09, "DKK": 0.134, "CZK": 0.04},
    },
    "roles": {"families": [], "hard_negative_regex": "", "dev_negative_regex": "", "extra_title_patterns": []},
    "profile": {"skills": [], "gaps": []},
    "scoring": {"weights": {"skills": 2.0, "location": 1.5, "pay": 1.0, "role": 2.0, "risk": 1.5},
                "skill_thresholds": [1, 3, 5, 7]},      # matches <=t -> dimension 1,2,3,4 ; more -> 5
    "shortlist": {"min_score": 4.0, "max_items": 10},
    "aggregators": {
        "enabled": ["We Work Remotely", "RemoteOK", "Remotive", "Arbeitnow", "Himalayas", "Jobicy", "Landing.jobs",
                    "Working Nomads", "Jobspresso", "NoDesk"],
        "himalayas": {"country": "Germany", "queries": ["it manager", "systems administrator", "project manager"]},
        "jobicy_geos": ["europe", "emea", "anywhere"],
        "wwr_categories": ["remote-devops-sysadmin-jobs", "remote-project-management-jobs", "remote-management-and-finance-jobs"],
        "remotive_categories": ["devops", "project-management", "business"],
    },
    "rotation": {"cadence_days": {"blocked": 60, "partial": 28, "explored": 42}},
}

_CFG = None

def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out

def find_path(path=None):
    for p in (path, os.environ.get("OPENROLES_CONFIG"), "config.json", os.path.join(ROOT, "config.example.json")):
        if p and os.path.exists(p):
            return p
    return None

def load(path=None):
    """Load and cache the configuration; returns the merged dict."""
    global _CFG
    p = find_path(path)
    user = json.load(open(p, encoding="utf-8")) if p else {}
    user.pop("_comment", None)
    _CFG = _merge(DEFAULTS, user); _CFG["_path"] = p
    return _CFG

def get():
    return _CFG if _CFG is not None else load()

def data_path(*parts):
    """Path inside data_dir (created on demand). Absolute data_dir allowed."""
    d = get()["data_dir"]
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, *parts)
