#!/usr/bin/env python3
"""Импорт подписей из выгрузки формы (Яндекс Формы, Google Формы и т. п.).

    python3 tools/import_form.py ответы.csv           показать, что будет добавлено
    python3 tools/import_form.py ответы.csv --write   дописать подписи в signatures.csv

Нужна выгрузка ответов в CSV. Столбцы узнаются по заголовкам вопросов:
«Имя и фамилия» (или «Подпись»), «Факультет», «Другое подразделение»,
«Курс или статус», «Согласие», а также служебные «ID» и «Время создания» /
«Отметка времени». Почта, логин и прочие личные данные не читаются и никуда
не попадают.

Повторный импорт той же или более новой выгрузки безопасен: ответы, которые
уже есть в списке, пропускаются (по номеру ответа, а если его нет — по дате,
имени и факультету).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signatures as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# порядок важен: более конкретные правила раньше общих
_COLUMNS = [
    ("form_id", re.compile(r"^(id|ид|номер ответа|id ответа|response id)$")),
    ("date", re.compile(r"врем|дата|timestamp|отметка")),
    ("faculty_other", re.compile(r"подразделени")),
    ("faculty", re.compile(r"факультет")),
    ("status", re.compile(r"курс|статус")),
    ("consent", re.compile(r"соглас")),
    ("name", re.compile(r"имя|подпис|фио|фамил")),
]
# столбцы с личными данными не читаем вовсе
_PRIVATE = re.compile(r"почт|mail|логин|login|телефон|phone|uid|user")

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S",
)


def detect_columns(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, title in enumerate(header):
        norm = re.sub(r"\s+", " ", title.casefold().replace("ё", "е")).strip()
        if _PRIVATE.search(norm):
            continue
        for key, pattern in _COLUMNS:
            if key not in found and pattern.search(norm):
                found[key] = index
                break
    return found


def parse_date(value: str) -> str | None:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return m.group(1) if m else None


def consent_value(value: str) -> bool:
    v = value.strip().casefold()
    return bool(v) and not v.startswith(("нет", "no", "false", "0"))


def read_export(path: Path) -> tuple[list[str], list[list[str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"{path}: не удалось прочитать файл — сохраните выгрузку в UTF-8")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        raise SystemExit(f"{path}: файл пуст")
    return rows[0], rows[1:]


def import_rows(
    header: list[str], rows: list[list[str]], existing: list[sg.Signature], options: sg.Options
) -> tuple[list[sg.Signature], list[str], int]:
    """Вернуть (новые подписи, сообщения об ошибках, сколько уже было в списке)."""
    columns = detect_columns(header)
    missing = [k for k in ("name", "faculty", "status", "date") if k not in columns]
    if missing:
        names = {"name": "имя", "faculty": "факультет", "status": "курс или статус", "date": "дата или время ответа"}
        raise SystemExit(
            "в выгрузке не нашлись столбцы: " + ", ".join(names[k] for k in missing)
            + ". Заголовки в файле: " + " | ".join(header)
        )
    ids = {s.id for s in existing}
    # подписи, пришедшие из формы автоматически (через заявки на GitHub), могут
    # не знать номера ответа — узнаём их по дате, имени и подразделению
    seen = {(s.date, s.name.casefold(), s.faculty) for s in existing if s.source == "form"}
    new, problems, already = [], [], 0
    for line, row in enumerate(rows, start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(key: str) -> str:
            index = columns.get(key)
            return row[index].strip() if index is not None and index < len(row) else ""

        if "consent" in columns and not consent_value(cell("consent")):
            problems.append(f"строка {line}: нет согласия на публикацию — пропущена")
            continue
        day = parse_date(cell("date"))
        if day is None:
            problems.append(f"строка {line}: не удалось разобрать дату «{cell('date')}»")
            continue
        fields = {k: cell(k) for k in ("name", "faculty", "faculty_other", "status")}
        sub, errors = sg.read_submission(fields, options, require_consent=False)
        if errors:
            problems.append(f"строка {line}: " + "; ".join(errors))
            continue
        form_id = re.sub(r"[^\w\-]", "", cell("form_id"))[:64]
        if form_id:
            sig_id = f"ya-{form_id}"
        else:
            digest = hashlib.sha1(f"{day}|{sub.name}|{sub.faculty}".encode()).hexdigest()[:12]
            sig_id = f"f-{digest}"
        key = (day, sub.name.casefold(), sub.faculty)
        if sig_id in ids or key in seen:
            already += 1
            continue
        ids.add(sig_id)
        seen.add(key)
        new.append(sg.Signature(sig_id, day, sub.name, sub.faculty, sub.status, "form"))
    return new, problems, already


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="выгрузка ответов формы в CSV")
    parser.add_argument("--write", action="store_true", help="дописать подписи в signatures.csv")
    args = parser.parse_args()

    options = sg.Options.load(ROOT / "options.toml")
    csv_path = ROOT / "signatures.csv"
    existing = sg.load(csv_path, options)
    header, rows = read_export(args.export)
    if "consent" not in detect_columns(header):
        print("В выгрузке нет столбца согласия: считаю, что форма не принимала ответы без него.")
    new, problems, already = import_rows(header, rows, existing, options)

    known = {(s.name.casefold(), s.faculty) for s in existing}
    for sig in new:
        mark = "  (такая подпись с этого факультета уже есть — проверьте)" if (sig.name.casefold(), sig.faculty) in known else ""
        print(f"+ {sig.date}  {sig.name} — {options.faculty_short(sig.faculty)}, {sig.status}{mark}")
    for problem in problems:
        print("! " + problem)
    print(f"\nНовых подписей: {len(new)}; уже в списке: {already}; с ошибками: {len(problems)}.")

    if args.write and new:
        sg.save(csv_path, existing + new)
        print("signatures.csv обновлён. Проверьте изменения (git diff) и отправьте их: git commit и git push.")
    elif new:
        print("Это предварительный просмотр. Чтобы записать подписи, добавьте --write.")


if __name__ == "__main__":
    main()
