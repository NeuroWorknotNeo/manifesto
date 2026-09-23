import tempfile
import unittest
from pathlib import Path

from helpers import CONSENT_OK, CONSENT_PARTIAL, OPTIONS, form_body

import signatures as sg


class NameTest(unittest.TestCase):
    def test_normalizes(self):
        self.assertEqual(sg.normalize_name("  иван   иванов "), ("Иван Иванов", None))
        self.assertEqual(sg.normalize_name("ИВАН ИВАНОВ-ПЕТРОВ"), ("Иван Иванов-Петров", None))
        self.assertEqual(sg.normalize_name("Иван И."), ("Иван И.", None))
        self.assertEqual(sg.normalize_name("Mary O’Neil"), ("Mary O'Neil", None))
        self.assertEqual(sg.normalize_name("Анна Мак-Лейн"), ("Анна Мак-Лейн", None))

    def test_rejects(self):
        for bad in ("", "Иван", "Иван 123", "http://spam.ru now", "=HYPERLINK(1) x",
                    "Ивaнов Иван",  # латинская «a» внутри кириллицы
                    "один два три четыре пять", "Я" * 61 + " Б"):
            with self.subTest(bad=bad):
                name, error = sg.normalize_name(bad)
                self.assertIsNone(name)
                self.assertTrue(error)


class OptionsTest(unittest.TestCase):
    def test_faculty_matching(self):
        self.assertEqual(OPTIONS.match_faculty("мехмат").short, "мехмат")
        self.assertEqual(OPTIONS.match_faculty("ВМК МГУ").short, "ВМК")
        self.assertEqual(OPTIONS.match_faculty("физический факультет").short, "физфак")
        self.assertEqual(OPTIONS.match_faculty("Факультет вычислительной математики и кибернетики").short, "ВМК")
        self.assertTrue(OPTIONS.match_faculty("Другое подразделение или филиал МГУ").other)
        self.assertIsNone(OPTIONS.match_faculty("МФТИ"))

    def test_status_matching(self):
        cases = {
            "3 курс (бакалавриат или специалитет)": "3 курс",
            "3": "3 курс",
            "третий курс": "3 курс",
            "Магистратура, 1 курс": "магистратура, 1 курс",
            "магистратура 2": "магистратура, 2 курс",
            "аспирант": "аспирантура",
            "Преподаватель МГУ": "преподаватель",
            "выпускница": "выпускник",
        }
        for raw, short in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(OPTIONS.match_status(raw).short, short)
        self.assertIsNone(OPTIONS.match_status("9 курс"))
        self.assertIsNone(OPTIONS.match_status("магистратура 3"))

    def test_every_status_has_group(self):
        for status in OPTIONS.statuses:
            self.assertIn(status.group, sg.GROUPS)


class IssueParsingTest(unittest.TestCase):
    def test_issue_form(self):
        fields = sg.parse_issue_form(form_body())
        self.assertEqual(fields["name"], "Иван Иванов")
        self.assertEqual(fields["faculty_other"], "")
        self.assertTrue(sg.consent_given(fields["consent"]))
        sub, errors = sg.read_submission(fields, OPTIONS)
        self.assertEqual(errors, [])
        self.assertEqual((sub.name, sub.faculty, sub.status), ("Иван Иванов", "Механико-математический факультет", "3 курс"))

    def test_consent_required(self):
        fields = sg.parse_issue_form(form_body(consent=CONSENT_PARTIAL))
        self.assertFalse(sg.consent_given(fields["consent"]))
        sub, errors = sg.read_submission(fields, OPTIONS)
        self.assertIsNone(sub)
        self.assertTrue(any("согласия" in e for e in errors))

    def test_other_unit(self):
        body = form_body(faculty="Другое подразделение или филиал МГУ", other="Филиал МГУ в Севастополе")
        sub, errors = sg.read_submission(sg.parse_issue_form(body), OPTIONS)
        self.assertEqual(errors, [])
        self.assertEqual(sub.faculty, "Филиал МГУ в Севастополе")
        body = form_body(faculty="Другое подразделение или филиал МГУ")
        sub, errors = sg.read_submission(sg.parse_issue_form(body), OPTIONS)
        self.assertIsNone(sub)

    def test_key_values(self):
        body = "Подпись из формы\nИмя: мария петрова\nФакультет: ВМК\nКурс: магистратура, 1 курс\nID ответа: 98765\n"
        fields = sg.parse_key_values(body)
        sub, errors = sg.read_submission(fields, OPTIONS, require_consent=False)
        self.assertEqual(errors, [])
        self.assertEqual((sub.name, sub.faculty, sub.status, sub.form_id),
                         ("Мария Петрова", "Факультет вычислительной математики и кибернетики", "магистратура, 1 курс", "98765"))

    def test_moscow_date(self):
        self.assertEqual(sg.moscow_date("2026-09-23T21:30:00Z"), "2026-09-24")
        self.assertEqual(sg.moscow_date("2026-09-23T20:59:59Z"), "2026-09-23")


class CsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "signatures.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        sigs = [
            sg.Signature("gh-1", "2026-09-24", "Иван Иванов", "Механико-математический факультет", "3 курс", "github", "gh:ivan"),
            sg.Signature("ya-77", "2026-09-25", "Анна С.", "Филиал МГУ в Севастополе", "аспирантура", "form"),
        ]
        sg.save(self.path, sigs)
        self.assertEqual(sg.load(self.path, OPTIONS), sigs)

    def test_committed_list_is_valid(self):
        from helpers import ROOT

        sg.load(ROOT / "signatures.csv", OPTIONS)

    def test_rejects_bad_rows(self):
        header = ",".join(sg.FIELDS) + "\n"
        bad_rows = [
            "x-1,2026-09-24,Иван Иванов,Физический факультет,3 курс,github,",
            "gh-1,24.09.2026,Иван Иванов,Физический факультет,3 курс,github,",
            "gh-1,2026-09-24,Иван,Физический факультет,3 курс,github,",
            "gh-1,2026-09-24,Иван Иванов,Физический факультет,7 курс,github,",
            "gh-1,2026-09-24,Иван Иванов,Другое подразделение или филиал МГУ,3 курс,github,",
            "gh-1,2026-09-24,Иван Иванов,Физический факультет,3 курс,mail,",
        ]
        for row in bad_rows:
            with self.subTest(row=row):
                self.path.write_text(header + row + "\n", encoding="utf-8")
                with self.assertRaises(sg.SignatureError):
                    sg.load(self.path, OPTIONS)

    def test_rejects_duplicates(self):
        header = ",".join(sg.FIELDS) + "\n"
        row = "gh-1,2026-09-24,Иван Иванов,Физический факультет,3 курс,github,gh:ivan\n"
        self.path.write_text(header + row + row, encoding="utf-8")
        with self.assertRaises(sg.SignatureError):
            sg.load(self.path, OPTIONS)


if __name__ == "__main__":
    unittest.main()
