"""Offline tests (no network): python3 -m unittest discover -s tests"""
import json, os, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openroles_radar import config, filters as F, scan, ats, factcheck, rotation, weekly


def J(title, loc, desc="", company="Acme", jid="1", remote=None):
    return ats.job(company, "greenhouse", jid, title, f"https://example.com/{jid}", loc, remote, desc)


def make_docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = config.load()                      # bundled config.example.json
        cfg["data_dir"] = self.tmp.name
    def tearDown(self):
        self.tmp.cleanup()


class TestFilters(Base):
    def test_location_classes(self):
        self.assertEqual(F.loc_class(J("x", "Berlin"))[0], "home_region")
        self.assertEqual(F.loc_class(J("x", "Remote, Germany"))[0], "home_country_remote")
        self.assertEqual(F.loc_class(J("x", "Remote - EMEA"))[0], "remote_emea_eu")
        self.assertTrue(F.loc_class(J("x", "New York, NY"))[0].startswith("excluded"))
        self.assertEqual(F.loc_class(J("x", "Remote"))[0], "remote_unspecified")

    def test_text_location_blocks_us_only(self):
        j = J("x", "Remote", "You must be authorized to work in the United States.")
        self.assertEqual(F.text_location(j, "remote_unspecified")[0], "exclude")

    def test_pay(self):
        j = J("x", "Remote", "Gross annual salary: EUR 40.000 - 45.000")
        txt, mx, kind = F.extract_pay(j)
        self.assertTrue(F.pay_below_threshold(mx))
        j = J("x", "Remote", "Salary: EUR 60000 - 70000 per year")
        self.assertFalse(F.pay_below_threshold(F.extract_pay(j)[1]))

    def test_role_and_score(self):
        j = J("Senior Systems Administrator", "Remote, Germany", "Linux, VMware, Active Directory, ITIL, monitoring and backup.")
        ok, fams, det, why = F.text_role(j)
        self.assertTrue(ok); self.assertEqual(fams[0], "infrastructure")
        sc = F.score_dims(j, fams, "home_country_remote", False, None)
        self.assertGreaterEqual(sc["total"], 3.5)
        self.assertFalse(F.text_role(J("Junior Sales Manager", "Berlin", "sales"))[0])


class TestState(Base):
    def test_fingerprint_collapses_republished(self):
        a = J("IT Manager", "Remote - Europe", company="Acme Careers")
        b = J("IT Manager", "Worldwide", company="Acme Inc")
        self.assertEqual(scan.fingerprint(a), scan.fingerprint(b))

    def test_incremental_skips_seen(self):
        jobs = [J("Systems Administrator", "Berlin", "Linux VMware Active Directory monitoring backup", jid="1"),
                J("Sales Director", "Berlin", "sales", jid="2")]
        for j in jobs: j["_src"] = "Acme"
        scan.gather = lambda *a, **k: ([{"level": "A", "source": "Acme", "type": "greenhouse", "slug": "acme", "total_postings": 2, "errors": "", "seconds": 0}],
                                        [dict(j) for j in jobs])
        t1 = scan.run(check_open=False)
        self.assertEqual((t1["new_processed"], t1["already_seen_skipped"]), (2, 0))
        self.assertEqual(t1["after_role"], 1)
        t2 = scan.run(check_open=False)
        self.assertEqual((t2["new_processed"], t2["already_seen_skipped"]), (0, 2))
        seen = json.load(open(os.path.join(self.tmp.name, "seen.json")))
        self.assertEqual(len(seen["items"]), 2)          # discarded postings are remembered too


class TestFactcheck(Base):
    def test_flags_invented_numbers(self):
        orig = os.path.join(self.tmp.name, "orig.docx"); cv = os.path.join(self.tmp.name, "cv.docx")
        make_docx(orig, ["Systems Administrator at Foo Ltd, March 2019 - May 2022. Managed 12 servers."])
        make_docx(cv, ["Systems Administrator at Foo Ltd, March 2019 - May 2022. Managed 12 servers.", "Reduced costs by 45 percent."])
        r = factcheck.check(cv, [orig])
        self.assertTrue(any(v == "45" for _, v in r["serious"]))
        self.assertFalse(factcheck.check(orig, [orig])["serious"])


class TestRotation(Base):
    def test_due(self):
        cov = {"sources": [{"id": 1, "name": "A", "url": "u", "status": "partial", "last_checked": None},
                           {"id": 2, "name": "B", "url": "u", "status": "partial", "last_checked": rotation._now().isoformat()}]}
        self.assertEqual([s["id"] for s in rotation.due(cov)], [1])


class TestWeekly(Base):
    def test_shortlist(self):
        c = [{"dedup": "new", "score": 4.5}, {"dedup": "new", "score": 3.0}, {"dedup": "handled", "score": 5.0}]
        sl, n_new, n_thr = weekly.shortlist(c)
        self.assertEqual((len(sl), n_new, n_thr), (1, 2, 1))


if __name__ == "__main__":
    unittest.main()
