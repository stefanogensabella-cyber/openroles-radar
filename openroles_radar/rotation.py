"""Rotation of sources WITHOUT a public API (browser-only career pages): do not revisit them every week.

`coverage.json` (in data_dir) lists them:
  {"sources": [{"id": 1, "name": "Example Corp", "url": "https://...", "status": "explored|partial|blocked", "last_checked": "ISO date or null"}]}

  python3 -m openroles_radar rotation due [--max 8]    sources whose cadence expired, most overdue first
  python3 -m openroles_radar rotation mark ID [ID...]  mark sources as checked now (backup of coverage.json is kept)
  python3 -m openroles_radar rotation mark-api         mark as checked the sources covered by level A (ATS API) in this round

Cadence in days from config['rotation']['cadence_days'] (by status). Sources covered by a verified ATS API
(companies.json) never appear in the 'due' list.
"""
import datetime, json, os, shutil, sys
from . import config

def _now(): return datetime.datetime.now().astimezone()

def cov_path(): return config.data_path("coverage.json")

def api_names():
    p = config.get()["companies_file"]
    if not os.path.isabs(p) and not os.path.exists(p): p = config.data_path(p)
    try:
        comp = json.load(open(p, encoding="utf-8"))["companies"]
    except Exception:
        return set()
    return {c["name"] for c in comp if any(h.get("verified") for h in c.get("ats", []))}

def due(cov, mx=8):
    cad = config.get()["rotation"]["cadence_days"]; skip = api_names(); now = _now(); out = []
    for s in cov["sources"]:
        if s["name"] in skip: continue
        last = datetime.datetime.fromisoformat(s["last_checked"]) if s.get("last_checked") else None
        if last and last.tzinfo is None: last = last.astimezone()
        d = cad.get(s.get("status"), 28)
        age = (now - last).days if last else 9999
        if age >= d: out.append((age - d, s))
    out.sort(key=lambda x: -x[0]); return [s for _, s in out[:mx]]

def main(argv):
    p = cov_path()
    if not os.path.exists(p):
        print("no coverage.json in", p, "(see README: Rotation)"); return 1
    cov = json.load(open(p, encoding="utf-8"))
    if argv and argv[0] == "due":
        mx = int(argv[argv.index("--max") + 1]) if "--max" in argv else 8
        for s in due(cov, mx): print(s["id"], s["name"], s.get("status", ""), s.get("last_checked") or "never", s["url"])
    elif argv and argv[0] in ("mark", "mark-api"):
        now = _now(); shutil.copy(p, p + f".bak-{now:%Y%m%d%H%M}")
        ids = set(map(int, argv[1:])) if argv[0] == "mark" else None; skip = api_names(); n = 0
        for s in cov["sources"]:
            if (ids is not None and s["id"] in ids) or (ids is None and s["name"] in skip):
                s["last_checked"] = now.isoformat(timespec="seconds"); n += 1
        json.dump(cov, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1); print("updated", n)
    else:
        print(__doc__)
    return 0
