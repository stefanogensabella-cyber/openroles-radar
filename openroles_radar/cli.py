"""Command line: python3 -m openroles_radar <command> [options]

  map        find and verify the ATS board of each company in companies.json (writes the result back)
  scan       one incremental round (ATS + aggregators), writes candidates/log into data_dir/output
  weekly     scan + shortlist-DATE.md (what a person or a model should read)
  rotation   due / mark / mark-api for sources without API
  factcheck  check an adapted CV against your original sources
  verify     probe one ATS board:  verify greenhouse airbnb
"""
import argparse, json, os, sys
from . import config

def _cmd_map(a):
    from . import ats, scan
    comp, path = scan.load_companies(a.companies)
    res = ats.build_map(comp)
    with open(path, "w", encoding="utf-8") as f: json.dump({"companies": res}, f, ensure_ascii=False, indent=1)
    for c in res:
        print(c["name"], "->", ", ".join(f"{h['ats']}:{h['slug']}{'' if h.get('verified') else '(unverified)'}" for h in c["ats"]) or "no public ATS found (browser rotation)")

def _cmd_verify(a):
    from . import ats
    ok, n, nm = ats.probe(a.ats, a.slug); print("exists:", ok, "postings:", n)
    if ok: print("verified:", ats.verify_hit(a.ats, a.slug, a.name or a.slug))

def main(argv=None):
    ap = argparse.ArgumentParser(prog="openroles_radar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.json (default: $OPENROLES_CONFIG, ./config.json, config.example.json)")
    ap.add_argument("--data-dir", help="override config data_dir (state and outputs live here)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("map"); p.add_argument("--companies"); p.set_defaults(f=_cmd_map)
    p = sp.add_parser("scan"); p.add_argument("--only-ats", action="store_true"); p.add_argument("--only-agg", action="store_true")
    p.add_argument("--reference", action="store_true", help="rebuild seen.json from scratch (first reference round)")
    p.add_argument("--companies"); p.add_argument("--no-open-check", action="store_true")
    p = sp.add_parser("weekly"); p.add_argument("--reference", action="store_true"); p.add_argument("--from-file"); p.add_argument("--companies")
    p = sp.add_parser("rotation"); p.add_argument("rest", nargs=argparse.REMAINDER)
    p = sp.add_parser("factcheck"); p.add_argument("rest", nargs=argparse.REMAINDER)
    p = sp.add_parser("verify"); p.add_argument("ats"); p.add_argument("slug"); p.add_argument("--name"); p.set_defaults(f=_cmd_verify)
    a = ap.parse_args(argv)
    cfg = config.load(a.config)
    if a.data_dir: cfg["data_dir"] = a.data_dir
    if a.cmd == "scan":
        from . import scan
        print(json.dumps(scan.run(a.only_ats, a.only_agg, a.reference, a.companies, not a.no_open_check), indent=1))
    elif a.cmd == "weekly":
        from . import weekly; weekly.main(a.reference, a.from_file, a.companies)
    elif a.cmd == "rotation":
        from . import rotation; return rotation.main(a.rest)
    elif a.cmd == "factcheck":
        from . import factcheck; return factcheck.main(a.rest)
    else:
        a.f(a)
    return 0
