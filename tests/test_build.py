import importlib.util
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import OPTIONS, ROOT

import build
import signatures as sg

HAVE_TOOLS = all(importlib.util.find_spec(m) for m in ("typst", "segno"))


class HelpersTest(unittest.TestCase):
    def test_plural(self):
        forms = ("подпись", "подписи", "подписей")
        cases = {1: "подпись", 2: "подписи", 5: "подписей", 11: "подписей", 12: "подписей",
                 21: "подпись", 22: "подписи", 111: "подписей", 1001: "подпись"}
        for n, word in cases.items():
            self.assertEqual(build.plural(n, *forms), word)

    def test_human_date(self):
        self.assertEqual(build.human_date(date(2026, 9, 3)).replace(" ", " "), "3 сентября 2026 г.")


class AssembleTest(unittest.TestCase):
    def test_groups_numbers_and_stats(self):
        project = build.load_project(use_env=False)
        sigs = [
            sg.Signature("gh-2", "2026-09-25", "Анна Б.", "Физический факультет", "аспирантура", "github", "gh:b"),
            sg.Signature("gh-1", "2026-09-24", "Иван А.", "Механико-математический факультет", "1 курс", "github", "gh:a"),
            sg.Signature("m-1", "2026-09-24", "Олег В.", "Механико-математический факультет", "преподаватель", "manual"),
            sg.Signature("f-9", "2026-09-26", "Ли Мин", "Филиал МГУ в Ташкенте", "1 курс", "form"),
        ]
        data = build.assemble(project, OPTIONS, sigs, date(2026, 9, 27))
        self.assertEqual(data["total"], 4)
        self.assertEqual([e["name"] for e in data["students"]], ["Иван А.", "Анна Б.", "Ли Мин"])
        self.assertEqual([e["n"] for e in data["students"]], [1, 2, 3])
        self.assertEqual(data["supporters"][0]["detail"], "мехмат, преподаватель")
        self.assertEqual(data["by_faculty"][0], {"faculty": "мехмат", "count": 2})
        self.assertEqual(data["faculties_total"], 3)
        self.assertTrue(data["total_label"].endswith("подписи"))

    def test_empty(self):
        project = build.load_project(use_env=False)
        data = build.assemble(project, OPTIONS, [], date(2026, 9, 27))
        self.assertEqual(data["total"], 0)
        self.assertIn("пока нет", data["summary"])


class IssueFormTest(unittest.TestCase):
    def test_committed_issue_form_is_up_to_date(self):
        project = build.load_project(use_env=False)
        for name, content in build.issue_form(project, OPTIONS).items():
            with self.subTest(name=name):
                committed = (ROOT / ".github" / "ISSUE_TEMPLATE" / name).read_text(encoding="utf-8")
                self.assertEqual(committed, content, "выполните: python3 tools/build.py issue-form")

    def test_form_labels_match_parser(self):
        form = build.issue_form(build.load_project(use_env=False), OPTIONS)["podpis.yml"]
        for label in sg.FORM_LABELS.values():
            self.assertIn(f'label: "{label}"', form)


@unittest.skipUnless(HAVE_TOOLS, "нужны пакеты typst и segno (pip install -r requirements.txt)")
class FullBuildTest(unittest.TestCase):
    def test_special_characters_compile(self):
        import mdlite
        import typst

        doc = mdlite.parse(
            'Знаки # $ // /* @ < > ~ = + - "кавычки" \\ и [скобки] — просто текст.\n\n'
            "- пункт с *курсивом* и `кодом`\n\n| a | b |\n|---|---|\n| $x$ | #y |\n"
        )
        folder = ROOT / "build" / "_test_escape"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            source = folder / "t.typ"
            source.write_text(mdlite.TypstWriter(doc.footnotes).blocks(doc.blocks), encoding="utf-8")
            pdf = typst.compile(str(source), root=str(ROOT), font_paths=[str(ROOT / "fonts")])
            self.assertTrue(pdf.startswith(b"%PDF"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_build_with_signatures(self):
        out = ROOT / "build" / "_test"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = Path(tmp) / "signatures.csv"
                sg.save(csv_path, [
                    sg.Signature("gh-1", "2026-09-24", "Иван Иванов", "Механико-математический факультет", "3 курс", "github", "gh:ivan"),
                    sg.Signature("ya-2", "2026-09-24", "Анна О'Нил", "Филиал МГУ в Севастополе", "выпускник", "form"),
                ])
                pdf = build.build(out=out, today=date(2026, 9, 27), signatures_csv=csv_path)
                self.assertGreater(pdf.stat().st_size, 20_000)
                index = (out / "site" / "index.html").read_text(encoding="utf-8")
                self.assertNotIn("{{", index)
                self.assertIn("Анна О&#x27;Нил", index)
                self.assertIn("2 подписи", index)
                for name in ("consent.html", "manifesto.pdf", "signatures.csv", "qr.svg"):
                    self.assertTrue((out / "site" / name).exists(), name)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
