import unittest

from helpers import OPTIONS

import import_form as imp
import signatures as sg

YANDEX_HEADER = [
    "ID", "Время создания", "Имя и фамилия", "Факультет", "Другое подразделение",
    "Курс или статус", "Университетская почта", "Согласие",
]


class ColumnsTest(unittest.TestCase):
    def test_detects_yandex_columns_and_skips_email(self):
        cols = imp.detect_columns(YANDEX_HEADER)
        self.assertEqual(cols["form_id"], 0)
        self.assertEqual(cols["date"], 1)
        self.assertEqual(cols["name"], 2)
        self.assertEqual(cols["faculty"], 3)
        self.assertEqual(cols["faculty_other"], 4)
        self.assertEqual(cols["status"], 5)
        self.assertEqual(cols["consent"], 7)
        self.assertNotIn(6, cols.values())

    def test_dates(self):
        self.assertEqual(imp.parse_date("2026-09-24 10:11:12"), "2026-09-24")
        self.assertEqual(imp.parse_date("24.09.2026 10:11"), "2026-09-24")
        self.assertEqual(imp.parse_date("9/24/2026 10:11:12"), "2026-09-24")
        self.assertIsNone(imp.parse_date("вчера"))


class ImportTest(unittest.TestCase):
    def rows(self):
        return [
            ["101", "2026-09-24 10:00:00", "иван иванов", "мехмат", "", "3 курс (бакалавриат или специалитет)", "ivan@my.msu.ru", "Согласен"],
            ["102", "2026-09-24 11:00:00", "Анна С.", "Другое подразделение или филиал МГУ", "Филиал МГУ в Ташкенте", "Аспирантура", "a@my.msu.ru", "Согласна"],
            ["103", "2026-09-24 12:00:00", "Пётр", "ВМК", "", "1 курс", "p@my.msu.ru", "Согласен"],
            ["104", "2026-09-24 13:00:00", "Ольга Петрова", "ВМК", "", "2 курс", "o@my.msu.ru", ""],
        ]

    def test_import(self):
        new, problems, already = imp.import_rows(YANDEX_HEADER, self.rows(), [], OPTIONS)
        self.assertEqual([s.id for s in new], ["ya-101", "ya-102"])
        self.assertEqual(new[0].name, "Иван Иванов")
        self.assertEqual(new[0].faculty, "Механико-математический факультет")
        self.assertEqual(new[1].faculty, "Филиал МГУ в Ташкенте")
        self.assertEqual(len(problems), 2)
        self.assertEqual(already, 0)
        for sig in new:  # почта никуда не попала
            self.assertNotIn("@", ",".join(vars(sig).values()))

    def test_repeat_import_is_idempotent(self):
        first, _, _ = imp.import_rows(YANDEX_HEADER, self.rows(), [], OPTIONS)
        again, _, already = imp.import_rows(YANDEX_HEADER, self.rows(), first, OPTIONS)
        self.assertEqual(again, [])
        self.assertEqual(already, 2)

    def test_skips_signatures_that_came_through_github(self):
        came = sg.Signature("gh-40", "2026-09-24", "Иван Иванов", "Механико-математический факультет", "3 курс", "form")
        new, _, already = imp.import_rows(YANDEX_HEADER, self.rows(), [came], OPTIONS)
        self.assertEqual([s.id for s in new], ["ya-102"])
        self.assertEqual(already, 1)

    def test_google_export_without_ids(self):
        header = ["Отметка времени", "Имя и фамилия", "Факультет", "Курс или статус"]
        rows = [["24.09.2026 10:00:00", "Мария Кюри", "химфак", "магистратура 1"]]
        new, problems, _ = imp.import_rows(header, rows, [], OPTIONS)
        self.assertEqual(problems, [])
        self.assertTrue(new[0].id.startswith("f-"))
        again, _, already = imp.import_rows(header, rows, new, OPTIONS)
        self.assertEqual((again, already), ([], 1))
        self.assertEqual(sg.validate(new[0], OPTIONS), [])


if __name__ == "__main__":
    unittest.main()
