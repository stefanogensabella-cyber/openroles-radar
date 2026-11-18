"""Weekly INCREMENTAL round: run the scan (skips everything already in seen.json), then write `shortlist-DATE.md`.

Only the shortlist is meant to be read by a person (or a language model): NEW candidates with score >= threshold,
at most N, each with a compact excerpt. The other new candidates stay in the CSV and in seen.json and are never
re-read or re-scored in later rounds.
"""
import datetime, json, os, re
from . import config

TODAY = datetime.date.today().isoformat()

def shortlist(cands, threshold=None, maxn=None):
    cfg = config.get()["shortlist"]
    threshold = cfg["min_score"] if threshold is None else threshold; maxn = cfg["max_items"] if maxn is None else maxn
    new = [c for c in cands if c["dedup"] == "new"]; over = [c for c in new if c["score"] >= threshold]
    over.sort(key=lambda c: -c["score"])
    return over[:maxn], len(new), len(over)

def snippet(c, w=700):
    d = re.sub(r"\s+", " ", c["desc"])
    m = re.search(r"(requirements|qualifications|what you.ll bring|who you are|we expect|you have|about you|must have)", d, re.I)
    s = m.start() if m else len(d) // 2
    return d[:350] + " [...] " + d[s:s + w]

def write_md(sl, n_new, n_thr, path):
    cfg = config.get()["shortlist"]
    L = [f"# Shortlist {TODAY}", "",
         f"New candidates (after the Python filters): {n_new}; with score >= {cfg['min_score']}: {n_thr}; in the shortlist (max {cfg['max_items']}): {len(sl)}.", "",
         "Read ONLY this excerpt for each item; evaluate in depth only if it looks worthwhile.", ""]
    for i, c in enumerate(sl, 1):
        L += [f"## {i}. {c['title']} - {c['company']} (score {c['score']})", f"- Link: {c['url']}",
              f"- Source: {c['source']} (level {c['level']}); location: {c['location']}; class: {c['location_class']}; reason: {c['location_reason']}",
              f"- Pay: {c['pay']}; families: {c['families']}; open check: {c.get('open_check', 'n/a')}",
              f"- Profile matches: {', '.join(c['profile_matches'][:8])}; risks: {'; '.join(c['risks'])}", f"- Excerpt: {snippet(c)}", ""]
    open(path, "w", encoding="utf-8").write("\n".join(L))

def main(reference=False, from_file=None, companies=None):
    from . import scan
    if from_file: f = from_file
    else:
        tot = scan.run(reference=reference, companies_path=companies); print(json.dumps(tot, indent=1))
        f = config.data_path("output", f"candidates-{TODAY}.json")
    cands = json.load(open(f, encoding="utf-8")); sl, nn, nt = shortlist(cands)
    out = config.data_path("output", f"shortlist-{TODAY}.md"); write_md(sl, nn, nt, out)
    print("shortlist:", out, len(sl), "of", nt)
