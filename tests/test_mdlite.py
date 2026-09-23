import unittest

from helpers import ROOT  # noqa: F401  (добавляет tools/ в путь)

import mdlite
from mdlite import NBSP, WORD_JOINER


class TypographTest(unittest.TestCase):
    def test_short_words_stick_to_next(self):
        self.assertEqual(mdlite.typograph("и в России"), f"и{NBSP}в{NBSP}России")
        self.assertEqual(mdlite.typograph("Это не так"), f"Это не{NBSP}так")

    def test_dash_sticks_to_previous_word(self):
        self.assertEqual(mdlite.typograph("ИИ — это"), f"ИИ{NBSP}— это")

    def test_initials_and_numbers(self):
        self.assertEqual(mdlite.typograph("М. В. Ломоносова"), f"М.{NBSP}В.{NBSP}Ломоносова")
        self.assertEqual(mdlite.typograph("40 000 человек"), f"40{NBSP}000{NBSP}человек")
        self.assertEqual(mdlite.typograph("10 %"), f"10{NBSP}%")
        self.assertEqual(mdlite.typograph("№ 124"), f"№{NBSP}124")
        self.assertEqual(mdlite.typograph("15–22"), f"15{WORD_JOINER}–{WORD_JOINER}22")

    def test_particles_stick_to_previous_word(self):
        self.assertEqual(mdlite.typograph("тот же"), f"тот{NBSP}же")

    def test_short_word_before_markup(self):
        nodes = mdlite.parse_inline("с **помощью** ИИ")
        self.assertEqual(nodes[0].value, f"с{NBSP}")


class InlineTest(unittest.TestCase):
    def test_emphasis_links_footnotes(self):
        nodes = mdlite.parse_inline("**жирный** и *курсив*, [ссылка](https://a.b/c_(d)) и сноска[^x]")
        kinds = [type(n).__name__ for n in nodes]
        self.assertEqual(kinds, ["Strong", "Text", "Emph", "Text", "Link", "Text", "FootnoteRef"])
        self.assertEqual(nodes[4].url, "https://a.b/c_(d)")
        self.assertEqual(nodes[6].key, "x")

    def test_lonely_asterisks_are_text(self):
        nodes = mdlite.parse_inline("5 * 3 * 2")
        self.assertEqual(mdlite.plain_text(nodes), "5 * 3 * 2")

    def test_escapes(self):
        self.assertEqual(mdlite.plain_text(mdlite.parse_inline(r"\*курсив\*")), "*курсив*")


class DocumentTest(unittest.TestCase):
    SOURCE = (
        "# Название\n\n*Подзаголовок*\n\nВводный абзац[^a].\n\n## Раздел\n\n"
        "- первый\n- второй\n\n1. раз\n2. два\n\n"
        "| A | B |\n|:--|--:|\n| x | 1 |\n\n> цитата\n\n[^a]: Источник, [сайт](https://example.org).\n"
    )

    def test_structure(self):
        doc = mdlite.parse(self.SOURCE)
        self.assertEqual(mdlite.plain_text(doc.title), "Название")
        self.assertEqual(mdlite.plain_text(doc.subtitle), "Подзаголовок")
        kinds = [type(b).__name__ for b in doc.blocks]
        self.assertEqual(kinds, ["Paragraph", "Heading", "ListBlock", "ListBlock", "Table", "Quote"])
        self.assertEqual(doc.blocks[4].aligns, ["left", "right"])
        self.assertIn("a", doc.footnotes)

    def test_footnote_errors(self):
        with self.assertRaises(mdlite.MarkdownError):
            mdlite.parse("Текст[^нет].\n")
        with self.assertRaises(mdlite.MarkdownError):
            mdlite.parse("Текст.\n\n[^лишняя]: никто не ссылается\n")

    def test_table_width_mismatch(self):
        with self.assertRaises(mdlite.MarkdownError):
            mdlite.parse("| A | B |\n|---|---|\n| только одна |\n")

    def test_typst_output_is_safe(self):
        doc = mdlite.parse('Знаки # $ // * " \\ и [скобки] остаются текстом.\n')
        out = mdlite.TypstWriter(doc.footnotes).blocks(doc.blocks)
        self.assertTrue(out.startswith('#"'))
        self.assertIn('\\"', out)
        self.assertIn("\\\\", out)

    def test_html_output_escapes(self):
        doc = mdlite.parse("Тег <b> и & [ссылка](https://x.org/?a=1&b=2)[^n]\n\n[^n]: Примечание.\n")
        writer = mdlite.HtmlWriter()
        writer.add_footnotes(doc.footnotes)
        html = writer.blocks(doc.blocks)
        self.assertIn("&lt;b&gt;", html)
        self.assertIn('href="https://x.org/?a=1&amp;b=2"', html)
        self.assertIn('href="#fn-n"', html)
        self.assertIn('id="fn-n"', writer.footnotes_html())

    def test_repeated_footnote_keeps_number(self):
        doc = mdlite.parse("Раз[^a], два[^a].\n\n[^a]: Одна сноска.\n")
        typ = mdlite.TypstWriter(doc.footnotes).blocks(doc.blocks)
        self.assertEqual(typ.count("#footnote["), 1)
        self.assertIn("#footnote(<fn-a>)", typ)


class RealTextsTest(unittest.TestCase):
    def test_texts_parse(self):
        for name in ("manifesto.md", "consent.md"):
            with self.subTest(name=name):
                mdlite.parse((ROOT / "text" / name).read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main()
