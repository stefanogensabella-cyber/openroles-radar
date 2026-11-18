"""Generic CV fact check: compares an adapted CV (DOCX) with your ORIGINAL sources (CV in DOCX/TXT/MD, profile notes).
Flags numbers, dates, roles, certifications/degrees, organizations, acronyms/tools and whole sentences that do NOT
appear in the sources, so an adapted CV never claims something you did not do.

Usage:  python3 -m openroles_radar factcheck ADAPTED_CV.docx --source original_cv.docx --source profile.md [--allow Canonical "Some Org"] [--json out.json]
Exit code: 0 = no serious findings; 1 = serious findings (numbers, dates, roles, certifications, organizations).
"""
import argparse, difflib, html, json, re, sys, zipfile

def docx_text(path):
    z = zipfile.ZipFile(path); out = []
    for name in z.namelist():
        if re.match(r"word/(document|header\d*|footer\d*)\.xml", name):
            x = z.read(name).decode("utf8", "replace")
            for p in re.findall(r"<w:p[ >].*?</w:p>", x, flags=re.S):
                t = html.unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)))
                if t.strip(): out.append(t)
    return "\n".join(out)

def reference_text(sources):
    """Concatenated text of the source files (.docx, or plain text/markdown)."""
    parts = []
    for p in sources:
        if p.lower().endswith(".docx"): parts.append(docx_text(p))
        else: parts.append(open(p, encoding="utf8", errors="replace").read())
    return "\n".join(parts)

MONTHS = {"jan": 1, "january": 1, "gen": 1, "gennaio": 1, "feb": 2, "february": 2, "febbraio": 2, "mar": 3, "march": 3, "marzo": 3, "apr": 4, "april": 4, "aprile": 4,
        "may": 5, "mag": 5, "maggio": 5, "jun": 6, "june": 6, "giu": 6, "giugno": 6, "jul": 7, "july": 7, "lug": 7, "luglio": 7, "aug": 8, "august": 8, "ago": 8, "agosto": 8,
        "sep": 9, "sept": 9, "september": 9, "set": 9, "settembre": 9, "oct": 10, "october": 10, "ott": 10, "ottobre": 10, "nov": 11, "november": 11, "novembre": 11,
        "dec": 12, "december": 12, "dic": 12, "dicembre": 12}
MON_RX = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?,? (?:del |de )?(20\d\d|19\d\d)\b", re.I)
ROLE_WORDS = r"(?:Manager|Lead|Head|Engineer|Consultant|Administrator|Chief|Specialist|Officer|Analyst|Architect|Director|Author|Writer|Coordinator|Owner|Helpdesk)"
ROLE_RX = re.compile(r"\b((?:[A-Z][A-Za-z&\-/]+ ){0,4}" + ROLE_WORDS + r"(?: (?:of|&|and|for|-|–) [A-Z][A-Za-z&\-/]+(?: [A-Z][A-Za-z&\-/]+){0,3})?)\b")
CERT_RX = re.compile(r"\b(CCNA|CCNP|CCIE|CISSP|CISM|CISA|CEH|PMP|CAPM|PRINCE2|ITIL ?(?:4|v3)[ -]?(?:certified|foundation)?|ITIL[ -](?:certified|foundation)|TOGAF|CompTIA|AWS Certified|Azure Certified|"
                     r"ISO ?27001 (?:Lead )?(?:Auditor|Implementer)|certified|certificat\w+|certificazion\w+|laurea|degree|graduated|diploma|master'?s|PhD|dottorato)\b", re.I)
ACR_RX = re.compile(r"\b([A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9/\.]*|[A-Za-z]+\d+[A-Za-z0-9]*|n8n|macOS|iOS)\b")
ORG_RX = re.compile(r"\b((?:[A-Z][A-Za-z0-9&\.\-]+ ){1,3}(?:S\.R\.L\.|SRL|S\.p\.A\.|Group|Ltd|GmbH|Inc\.?|LLC|AG|PLC))\b")

def norm(s): return re.sub(r"[^a-z0-9]+", "", s.lower())

def feats(text):
    f = {}
    f["numbers"] = sorted({re.sub(r"[.,](?=\d{3}\b)", "", n) for n in re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)*", text)})
    f["dates"] = sorted({(MONTHS[m.group(1).lower()], m.group(2)) for m in MON_RX.finditer(text)})
    f["roles"] = sorted({m.group(1).strip() for m in ROLE_RX.finditer(text)})
    f["certs"] = sorted({m.group(1).lower() for m in CERT_RX.finditer(text)})
    f["acronyms"] = sorted({m.group(1) for m in ACR_RX.finditer(text) if len(m.group(1)) > 1})
    f["orgs"] = sorted({m.group(1).strip() for m in ORG_RX.finditer(text)})
    return f

def sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", t) if len(s.strip()) > 25]

def check(cv_path, sources, allow=()):
    cv = docx_text(cv_path); ref = reference_text(sources)
    rn = norm(ref); allow_n = [norm(a) for a in allow]
    fc, fr = feats(cv), feats(ref)
    grave, info = [], []
    refnums = set(fr["numbers"])
    for n in fc["numbers"]:
        if n not in refnums and not any(n in norm(a) for a in allow): grave.append(("number not in the sources", n))
    refdates = set(fr["dates"])
    for d in fc["dates"]:
        if d not in refdates:
            # same year with a different month is still serious
            grave.append(("date (month, year) not in the sources", f"{d[0]:02d}/{d[1]}"))
    for r in fc["roles"]:
        if norm(r) in rn or any(norm(r) in a or a in norm(r) for a in allow_n):
            continue
        words = [w for w in re.findall(r"[A-Za-z]{3,}", r) if w.lower() not in ("and", "for", "the")]
        refwords = set(re.findall(r"[a-z]{3,}", ref.lower()))
        if words and all(w.lower() in refwords for w in words):
            info.append(("role whose words all appear in the sources but not as the same phrase (verify)", r))
        else:
            grave.append(("role/title not in the sources", r))
    neg = re.compile(r"\b(no|not|never|without|non|nessun\w*|senza|does not|do not)\b", re.I)
    cert_aff = set()
    for sent in re.split(r"(?<=[.!?;])\s+|\n+", cv):
        if neg.search(sent): continue
        for m in CERT_RX.finditer(sent): cert_aff.add(m.group(1).lower())
    for c in fc["certs"]:
        if c not in cert_aff: info.append(("certification/degree mentioned only in a negative sentence (ok)", c)); continue
        if c not in fr["certs"]: grave.append(("certification/degree claimed but not in the sources", c))
        else: info.append(("certification/degree present in the sources (verify it is not claimed as obtained if it is not)", c))
    for o in fc["orgs"]:
        if norm(o) not in rn and not any(norm(o) in a or a in norm(o) for a in allow_n): grave.append(("organization not in the sources", o))
    for s in fc["acronyms"]:
        if norm(s) not in rn and not any(norm(s) in a for a in allow_n): info.append(("acronym/tool not in the sources", s))
    rs = sentences(ref); new = []
    for s in sentences(cv):
        best = max((difflib.SequenceMatcher(None, s.lower(), r.lower()).ratio() for r in rs), default=0)
        toks = set(re.findall(r"[a-zàèéìòù0-9]{4,}", s.lower()))
        cover = len([t for t in toks if t in ref.lower()]) / max(1, len(toks))
        if best < 0.75 and cover < 0.9: new.append((round(best, 2), round(cover, 2), s))
    return {"cv": cv_path, "serious": grave, "info": info, "new_sentences_to_review": new}

def main(argv=None):
    ap = argparse.ArgumentParser(prog="factcheck", description=__doc__.split("\n")[0])
    ap.add_argument("cv"); ap.add_argument("--source", action="append", required=True, help="original CV / profile file (.docx, .txt, .md); repeatable")
    ap.add_argument("--allow", nargs="*", default=[], help="terms that are allowed even if absent from the sources (e.g. the target company name)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    r = check(a.cv, a.source, a.allow)
    print(f"# Fact check: {a.cv}")
    print(f"Serious findings: {len(r['serious'])}")
    for k, v in r["serious"]: print(f"  [SERIOUS] {k}: {v}")
    print(f"Notes: {len(r['info'])}")
    for k, v in r["info"][:30]: print(f"  [note] {k}: {v}")
    print(f"Sentences not traceable to the sources (review): {len(r['new_sentences_to_review'])}")
    for b, c, snt in r["new_sentences_to_review"][:25]: print(f"  [sentence] sim={b} coverage={c}: {snt[:200]}")
    if a.json: json.dump(r, open(a.json, "w"), ensure_ascii=False, indent=1)
    return 1 if r["serious"] else 0

if __name__ == "__main__":
    sys.exit(main())
