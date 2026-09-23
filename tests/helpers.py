"""Общее для тестов: пути и образцы заявок."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import signatures as sg  # noqa: E402

OPTIONS = sg.Options.load(ROOT / "options.toml")

CONSENT_OK = (
    "- [X] Я учусь или работаю в МГУ либо окончил(а) его и поддерживаю предложение манифеста.\n"
    "- [X] Я согласен(на) на публикацию имени, факультета, курса и даты подписи в открытом списке подписавших."
)
CONSENT_PARTIAL = CONSENT_OK.replace("- [X] Я согласен", "- [ ] Я согласен")


def form_body(
    name="Иван Иванов",
    faculty="Механико-математический факультет",
    other="_No response_",
    status="3 курс (бакалавриат или специалитет)",
    consent=CONSENT_OK,
):
    """Тело заявки в том виде, в каком GitHub сохраняет ответ на форму."""
    return (
        f"### Имя и фамилия\n\n{name}\n\n"
        f"### Факультет\n\n{faculty}\n\n"
        f"### Другое подразделение\n\n{other}\n\n"
        f"### Курс или статус\n\n{status}\n\n"
        f"### Согласие\n\n{consent}\n"
    )


def issue(number, body, login="student", created="2026-09-24T09:15:00Z"):
    return {"number": number, "body": body, "user": {"login": login}, "created_at": created}
