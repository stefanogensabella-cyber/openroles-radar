# openroles-radar

An **incremental, read-only radar for public job postings**. It reads the public job-board APIs of the major ATS
vendors and a handful of public aggregator feeds, filters by location / role / pay, de-duplicates, scores, and keeps
a `seen.json` memory so each round only processes what is **new**.

- **Pure Python standard library** (Python ≥ 3.9). No dependencies, no browser, no API keys.
- **ATS adapters**: Greenhouse, Lever, Ashby, SmartRecruiters, Teamtailor, Workable, Recruitee, Personio.
- **Aggregators** (public feeds): We Work Remotely, RemoteOK, Remotive, Arbeitnow, Himalayas, Jobicy, Landing.jobs,
  Working Nomads, Jobspresso, NoDesk.
- **Filters**: structured location + full-text location (ambiguous cases are flagged, not dropped), role families
  (title + description), declared-pay floor (with currency conversion).
- **Incremental state**: `seen.json` stores key + fingerprint + date of *every* posting seen (even discarded ones);
  later rounds skip them before downloading details or scoring.
- **Fingerprint dedup** (`company|title|city`) catches the same job republished elsewhere; `handled.json` holds
  postings you already dealt with.
- **Scoring**: an internal 1–5 score used **only to order** candidates, never to discard them.
- **Rotation** for sources that have no API (career pages you check by hand): cadence by status, with last-check dates.
- **Weekly shortlist**: a compact Markdown file with the few new, high-scoring candidates worth reading.
- **Generic CV fact check**: compares an adapted CV (DOCX) against your original sources and flags numbers, dates,
  roles, certifications, organizations and sentences that are not in them.

## Ethical principles

1. **Only public APIs and feeds.** No login, no cookies, no captcha handling, no scraping behind walls. If a source
   blocks the request, the error is logged and the source is skipped — never circumvented.
2. **Polite by default.** A per-host rate limit (`min_interval_seconds`, default 0.25 s), few calls per aggregator, a
   descriptive User-Agent, and incremental rounds that avoid re-downloading what you already saw.
3. **Attribution kept.** Source name and the link to the original posting are kept on every row; follow each site's terms.
4. **Never automatic applications.** The tool finds and ranks postings. Nothing is ever sent, posted or submitted on your
   behalf; you read, decide and apply yourself.
5. **Honest CVs.** The fact checker exists so adapted CVs never claim anything not in your real CV.

## Install

```bash
git clone https://github.com/stefanogensabella-cyber/openroles-radar.git
cd openroles-radar
python3 --version          # >= 3.9; nothing else to install
cp config.example.json config.json         # then edit it
cp companies.example.json data/companies.json   # or let `map` discover ATS boards for your own list
```

Optional: `pip install .` gives you an `openroles-radar` command (same as `python3 -m openroles_radar`).

## Usage

```bash
python3 -m openroles_radar map                       # discover + verify the ATS board of each company in companies.json
python3 -m openroles_radar scan --reference          # first round: builds seen.json from scratch
python3 -m openroles_radar scan                      # later rounds: only NEW postings are processed
python3 -m openroles_radar scan --only-ats           # skip aggregators   (--only-agg: skip company boards)
python3 -m openroles_radar weekly                    # scan + output/shortlist-DATE.md
python3 -m openroles_radar rotation due --max 8      # no-API sources to check by hand this week
python3 -m openroles_radar rotation mark 3 5         # mark them as checked
python3 -m openroles_radar factcheck cv_adapted.docx --source cv_original.docx --source profile.md
python3 -m openroles_radar verify greenhouse gitlab  # probe one board
```

Global options: `--config path/to/config.json`, `--data-dir path` (state and outputs live in the data dir; default `./data`).

Outputs in `data_dir/output/`: `candidates-DATE.csv|json`, `scan-log-DATE.json`, `totals-DATE.json`, `shortlist-DATE.md`.
State in `data_dir/`: `seen.json`, optional `handled.json`, `coverage.json`, `companies.json`.

Run the offline tests with `python3 -m unittest discover -s tests`.

### Typical weekly flow

1. `weekly` → read only `shortlist-DATE.md` (new candidates above the threshold, max 10 by default).
2. Evaluate the few that look worthwhile, apply by hand, add them to `handled.json` so they never come back.
3. `rotation due` → look at the no-API career pages that are due, then `rotation mark`.

## Configuration

`config.example.json` is a complete, **generic** example (a Berlin-based IT/infrastructure/project profile with a
50 000 EUR floor). Copy it to `config.json`; missing keys fall back to built-in defaults. Lookup order: `--config`,
`$OPENROLES_CONFIG`, `./config.json`, bundled `config.example.json`.

| Key | Meaning |
| --- | --- |
| `data_dir`, `companies_file` | where state/outputs live; the companies list |
| `min_interval_seconds`, `user_agent` | politeness settings (please put a contact URL in the User-Agent) |
| `deny_slugs` | `[["ats","slug"], ...]` known homonyms the slug verifier must never accept |
| `location.home_country_terms / home_country_cities / home_region_terms` | where you can work; the region gets the top score |
| `location.broad_terms / non_home_terms / european_countries` | remote-region vocabulary |
| `salary.min_annual_eur`, `salary.fx_to_eur` | employee roles with a declared maximum below the floor are silently dropped |
| `roles.families` | list of `{name, primary, title_patterns:[[regex, weight]], text_pattern}`; weights ≥ 3 = strong title match |
| `roles.hard_negative_regex / dev_negative_regex` | titles to drop / de-prioritize |
| `profile.skills` / `profile.gaps` | `[regex, label]` for matching skills, and `[regex, label, "weak"?]` for risks |
| `scoring`, `shortlist` | weights, skill thresholds, shortlist threshold and size |
| `aggregators` | enabled feeds, Himalayas queries/country, Jobicy geos, WWR and Remotive categories |
| `rotation.cadence_days` | days between checks by source status |

`companies.json`: `{"companies":[{"name":..., "careers_url":..., "ats":[{"ats":"greenhouse","slug":"...","verified":true}]}]}`.
See `companies.example.json` (8 public companies). Only entries with `verified: true` are scanned. `map` fills `ats` from
slugs found on the careers page (strong evidence) or from guessed slugs checked against the board data (to avoid homonyms).

`coverage.json` (rotation): `{"sources":[{"id":1,"name":"...","url":"...","status":"explored|partial|blocked","last_checked":null}]}`.

## Limits

- Free-text filters (location, role, pay) are heuristics: read the shortlist, do not trust the score blindly.
- Public feeds change or get protected without notice; failing sources are reported in the scan log.
- Sources that require login or forbid automated access (e.g. LinkedIn, Indeed) are intentionally not supported.

## Credits

Inspired by [career-ops](https://github.com/career-ops-hq/career-ops) by Santiago Fernández de Valderrama (MIT),
https://github.com/career-ops-hq/career-ops; not affiliated — career-ops is a trademark of its owner.
This repository is an independent implementation written from scratch; no career-ops source code is included.

## Licence

MIT © 2026 Stefano Gensabella — see [LICENSE](LICENSE).

---

## In breve (italiano)

**openroles-radar** è un radar *incrementale e in sola lettura* per offerte di lavoro pubbliche. Legge le API pubbliche
dei principali ATS (Greenhouse, Lever, Ashby, SmartRecruiters, Teamtailor, Workable, Recruitee, Personio) e alcuni feed
pubblici di aggregatori, filtra per luogo, ruolo e retribuzione dichiarata, elimina i duplicati (impronta
azienda+ruolo+città), assegna un punteggio interno (solo per ordinare) e conserva `seen.json`: ad ogni giro vengono
elaborate **solo le offerte nuove**. Include rotazione delle fonti senza API, shortlist settimanale e un controllo dei
fatti per i CV adattati (nessun numero, data, ruolo o certificazione inventati).

- **Requisiti**: Python ≥ 3.9, solo libreria standard. **Uso**: `python3 -m openroles_radar scan --reference`, poi `weekly`.
- **Configurazione**: copia `config.example.json` in `config.json` e adatta luogo, famiglie di ruolo, competenze, soglia RAL.
- **Principi**: solo API pubbliche, nessun login, rate limit, **mai candidature automatiche**: decidi e invii tu.
- **Credito**: ispirato a career-ops di Santiago Fernández de Valderrama (MIT); non affiliato.
