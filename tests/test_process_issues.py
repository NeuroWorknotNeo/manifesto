import unittest

from helpers import CONSENT_PARTIAL, OPTIONS, form_body, issue

import build
import process_issues as pi
import signatures as sg

PROJECT = build.load_project(use_env=False)


def existing(sig_id="gh-1", account="gh:first"):
    return sg.Signature(sig_id, "2026-09-24", "Пётр Первый", "Физический факультет", "2 курс", "github", account)


class DecideTest(unittest.TestCase):
    def test_accepts_valid_signature(self):
        [d] = pi.decide([issue(5, form_body(), login="Ivan-Ivanov")], [], OPTIONS)
        self.assertEqual(d.kind, "accept")
        self.assertEqual(d.signature.id, "gh-5")
        self.assertEqual(d.signature.account, "gh:ivan-ivanov")
        self.assertEqual(d.signature.date, "2026-09-24")
        self.assertEqual(d.signature.source, "github")

    def test_ignores_ordinary_issues(self):
        self.assertEqual(pi.decide([issue(3, "Опечатка во втором абзаце")], [], OPTIONS), [])

    def test_one_signature_per_account(self):
        decisions = pi.decide(
            [issue(7, form_body(), login="first"), issue(8, form_body(name="Иван Другой"), login="second"),
             issue(9, form_body(name="Иван Второй"), login="second")],
            [existing()],
            OPTIONS,
        )
        self.assertEqual([d.kind for d in decisions], ["duplicate", "accept", "duplicate"])
        self.assertEqual(decisions[0].existing_id, "gh-1")
        self.assertEqual(decisions[2].existing_id, "gh-8")

    def test_already_in_list(self):
        [d] = pi.decide([issue(1, form_body(), login="first")], [existing()], OPTIONS)
        self.assertEqual(d.kind, "already")

    def test_rejects_invalid(self):
        decisions = pi.decide(
            [issue(2, form_body(consent=CONSENT_PARTIAL)), issue(3, form_body(name="Иван"), login="other")],
            [],
            OPTIONS,
        )
        self.assertEqual([d.kind for d in decisions], ["reject", "reject"])
        self.assertTrue(decisions[1].reasons)

    def test_form_bot_issues(self):
        body = "Имя: Анна Смирнова\nФакультет: мехмат\nКурс: 1 курс\nID ответа: 555\n"
        decisions = pi.decide(
            [issue(11, body, login="form-bot"), issue(12, body, login="form-bot")],
            [],
            OPTIONS,
            form_bot="Form-Bot",
        )
        self.assertEqual([d.kind for d in decisions], ["accept", "already"])
        self.assertEqual(decisions[0].signature.id, "ya-555")
        self.assertEqual(decisions[0].signature.account, "")
        self.assertEqual(decisions[0].signature.source, "form")

    def test_form_bot_account_can_sign_with_issue_form(self):
        [d] = pi.decide([issue(13, form_body(), login="form-bot")], [], OPTIONS, form_bot="form-bot")
        self.assertEqual((d.kind, d.from_form, d.signature.account), ("accept", False, "gh:form-bot"))

    def test_key_value_body_from_stranger_is_ignored(self):
        body = "Имя: Анна Смирнова\nФакультет: мехмат\nКурс: 1 курс\n"
        self.assertEqual(pi.decide([issue(4, body, login="stranger")], [], OPTIONS, form_bot="form-bot"), [])


class ReplyTest(unittest.TestCase):
    def test_numbers_follow_build_order(self):
        sigs = [
            sg.Signature("gh-2", "2026-09-26", "Б Б", "Физический факультет", "1 курс", "github", "gh:b"),
            sg.Signature("gh-1", "2026-09-25", "А А", "Физический факультет", "1 курс", "github", "gh:a"),
            sg.Signature("gh-3", "2026-09-25", "В В", "Физический факультет", "выпускник", "github", "gh:c"),
        ]
        self.assertEqual(pi.group_numbers(sigs, OPTIONS), {"gh-1": 1, "gh-2": 2, "gh-3": 1})

    def test_texts(self):
        [d] = pi.decide([issue(5, form_body())], [], OPTIONS)
        text = pi.reply_text(d, [d.signature], OPTIONS, PROJECT)
        self.assertIn("под номером 1", text)
        self.assertIn(PROJECT.site_url, text)
        self.assertIn(pi.MARKER, text)
        reject = pi.Decision(6, "reject", reasons=["не указаны имя и фамилия"])
        self.assertIn(PROJECT.github_sign_url, pi.reply_text(reject, [], OPTIONS, PROJECT))
        form = pi.Decision(7, "accept", from_form=True, signature=d.signature)
        self.assertIsNone(pi.reply_text(form, [d.signature], OPTIONS, PROJECT))


if __name__ == "__main__":
    unittest.main()
