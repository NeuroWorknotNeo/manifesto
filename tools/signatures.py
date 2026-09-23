"""Подписи: хранение, проверка и разбор заявок.

signatures.csv — единственный источник правды о подписях. Столбцы:

  id       уникальный ключ: gh-<номер заявки на GitHub>, ya-<номер ответа
           Яндекс Формы>, f-<хеш> для выгрузки формы без номеров ответов,
           m-<что угодно> для подписей, добавленных вручную
  date     дата подписи, ГГГГ-ММ-ДД, по московскому времени
  name     подпись ровно в том виде, в каком она публикуется
  faculty  полное название факультета из options.toml или, для «другого
           подразделения», название, которое написал подписавший
  status   краткий статус из options.toml, например «3 курс»
  source   github | form | manual
  account  gh:<логин> для подписей через GitHub — чтобы с одной учётной
           записи нельзя было подписать дважды; для остальных пусто
"""

from __future__ import annotations

import csv
import re
import tomllib
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FIELDS = ("id", "date", "name", "faculty", "status", "source", "account")
SOURCES = ("github", "form", "manual")
GROUPS = ("students", "supporters")
MOSCOW = timezone(timedelta(hours=3))

# Поля формы подписи на GitHub: заголовок поля в форме -> ключ.
# Эти же заголовки использует генератор формы (tools/build.py issue-form).
FORM_LABELS = {
    "name": "Имя и фамилия",
    "faculty": "Факультет",
    "faculty_other": "Другое подразделение",
    "status": "Курс или статус",
    "consent": "Согласие",
}

# Заявки, которые создаёт интеграция формы (строки вида «Факультет: мехмат»).
KEY_SYNONYMS = {
    "имя": "name",
    "имя и фамилия": "name",
    "фамилия и имя": "name",
    "подпись": "name",
    "фио": "name",
    "факультет": "faculty",
    "подразделение": "faculty_other",
    "другое подразделение": "faculty_other",
    "курс": "status",
    "статус": "status",
    "курс или статус": "status",
    "id ответа": "form_id",
    "номер ответа": "form_id",
    "id": "form_id",
    "согласие": "consent",
}


class SignatureError(ValueError):
    """Ошибка в данных; сообщение пригодно для показа человеку."""


# --------------------------------------------------------------- справочники


@dataclass(frozen=True)
class Faculty:
    name: str
    short: str
    aliases: tuple = ()
    other: bool = False


@dataclass(frozen=True)
class Status:
    label: str
    short: str
    group: str


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'().,;:!?/]", " ", text)
    text = re.sub(r"\b(мгу|имени|им|м в ломоносова|ломоносова)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_faculty(text: str) -> str:
    text = _norm(text).replace("ф-т", " ")
    text = re.sub(r"\b(факультет|фак)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6}


class Options:
    """Факультеты и статусы из options.toml."""

    def __init__(self, faculties: list[Faculty], statuses: list[Status]):
        self.faculties = faculties
        self.statuses = statuses
        others = [f for f in faculties if f.other]
        if len(others) != 1:
            raise SignatureError("options.toml: должен быть ровно один пункт с other = true")
        self.other = others[0]
        self._by_name = {f.name: f for f in faculties}
        self._by_short = {s.short: s for s in statuses}
        if len(self._by_name) != len(faculties):
            raise SignatureError("options.toml: названия факультетов повторяются")
        if len(self._by_short) != len(statuses):
            raise SignatureError("options.toml: краткие статусы повторяются")
        for s in statuses:
            if s.group not in GROUPS:
                raise SignatureError(f"options.toml: у статуса «{s.label}» неизвестная группа {s.group!r}")
        self._faculty_keys: dict[str, Faculty] = {}
        for f in faculties:
            for key in (f.name, f.short, *f.aliases):
                if key:
                    for variant in {_norm(key), _norm_faculty(key)}:
                        if variant:
                            self._faculty_keys.setdefault(variant, f)

    @classmethod
    def load(cls, path: Path) -> "Options":
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        faculties = [
            Faculty(
                name=f["name"],
                short=f.get("short", ""),
                aliases=tuple(f.get("aliases", ())),
                other=bool(f.get("other", False)),
            )
            for f in raw.get("faculty", [])
        ]
        statuses = [Status(s["label"], s["short"], s["group"]) for s in raw.get("status", [])]
        return cls(faculties, statuses)

    def faculty_by_name(self, name: str) -> Faculty | None:
        return self._by_name.get((name or "").strip())

    def match_faculty(self, text: str) -> Faculty | None:
        """Факультет по названию, сокращению или другому написанию."""
        exact = self.faculty_by_name(text)
        if exact:
            return exact
        for variant in (_norm(text), _norm_faculty(text)):
            if variant in self._faculty_keys:
                return self._faculty_keys[variant]
        return None

    def status_by_short(self, short: str) -> Status | None:
        return self._by_short.get((short or "").strip())

    def match_status(self, text: str) -> Status | None:
        """Статус по подписи из формы («3 курс (бакалавриат…)») или по вольному
        написанию («3», «третий курс», «магистратура 1», «аспирант»)."""
        text = (text or "").strip()
        for s in self.statuses:
            if text in (s.label, s.short):
                return s
        t = _norm(text)
        for s in self.statuses:
            if t in (_norm(s.label), _norm(s.short)):
                return s

        def by_short(short: str) -> Status | None:
            return self._by_short.get(short)

        if "асп" in t:
            return by_short("аспирантура")
        if "препод" in t:
            return by_short("преподаватель")
        if "сотрудн" in t or "научн" in t:
            return by_short("сотрудник")
        if "выпуск" in t:
            return by_short("выпускник")
        digit = re.search(r"(?<!\d)([1-6])(?!\d)", t)
        number = int(digit.group(1)) if digit else None
        if number is None:
            for stem, value in _ORDINALS.items():
                if stem in t:
                    number = value
                    break
        if number is None:
            return None
        if "маг" in t:
            return by_short(f"магистратура, {number} курс") if number in (1, 2) else None
        return by_short(f"{number} курс")

    def group_of(self, status_short: str) -> str:
        status = self.status_by_short(status_short)
        if status is None:
            raise SignatureError(f"неизвестный статус «{status_short}»")
        return status.group

    def faculty_short(self, faculty: str) -> str:
        """Как факультет выглядит в списке подписей."""
        known = self.faculty_by_name(faculty)
        if known and not known.other:
            return known.short or known.name
        return faculty


# -------------------------------------------------------- проверка полей

_NAME_CHARS = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿĀ-žА-Яа-яЁё .'\-]+$")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]")


def _fix_case(word: str) -> str:
    """«иванов» и «ИВАНОВ» -> «Иванов»; смешанный регистр не трогаем."""
    parts = re.split(r"([\-'])", word)
    fixed = []
    for part in parts:
        letters = part.rstrip(".")
        if letters and (letters.islower() or (letters.isupper() and len(letters) > 1)):
            part = letters[0].upper() + letters[1:].lower() + part[len(letters):]
        fixed.append(part)
    return "".join(fixed)


def normalize_name(raw: str) -> tuple[str | None, str | None]:
    """Привести подпись к виду «Иван Иванов». Возвращает (подпись, ошибка)."""
    s = unicodedata.normalize("NFC", raw or "")
    for quote in ("’", "‘", "ʼ", "`"):
        s = s.replace(quote, "'")
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*-\s*", "-", s)
    if not s:
        return None, "не указаны имя и фамилия"
    if len(s) > 60:
        return None, "подпись длиннее 60 знаков"
    if not _NAME_CHARS.match(s):
        return None, "в подписи можно использовать только буквы (кириллицу или латиницу), пробел, дефис, точку и апостроф"
    words = s.split(" ")
    if len(words) < 2:
        return None, "нужны имя и фамилия (можно сокращённо: «Иван И.»)"
    if len(words) > 4:
        return None, "в подписи больше четырёх слов: достаточно имени и фамилии"
    for word in words:
        if not re.search(r"[^\W\d_]", word):
            return None, f"в слове «{word}» нет букв"
        if not word[0].isalpha():
            return None, f"слово «{word}» должно начинаться с буквы"
        if _CYRILLIC.search(word) and _LATIN.search(word):
            return None, f"в слове «{word}» смешаны кириллица и латиница"
    return " ".join(_fix_case(w) for w in words), None


_UNIT_CHARS = re.compile(r"^[0-9A-Za-zА-Яа-яЁё .,\-«»\"()№]+$")


def normalize_unit(raw: str) -> tuple[str | None, str | None]:
    """Название подразделения, которое подписавший написал сам."""
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw or "")).strip()
    if not s:
        return None, "выбрано «Другое подразделение», но не написано, какое"
    if len(s) > 80:
        return None, "название подразделения длиннее 80 знаков"
    if re.search(r"https?:|www\.|@", s, re.I):
        return None, "в названии подразделения не должно быть ссылок и адресов"
    if not _UNIT_CHARS.match(s) or not (s[0].isalpha() or s[0] == "«"):
        return None, "в названии подразделения можно использовать буквы, цифры и обычные знаки препинания"
    return s, None


def moscow_date(iso: str) -> str:
    """'2026-09-23T21:30:00Z' -> '2026-09-24' (дата по Москве)."""
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MOSCOW).date().isoformat()


# ------------------------------------------------------------------- CSV


@dataclass
class Signature:
    id: str
    date: str
    name: str
    faculty: str
    status: str
    source: str
    account: str = ""


_ID = re.compile(r"^(gh|ya|f|m)-[\w\-]{1,64}$")
_ACCOUNT = re.compile(r"^gh:[a-z0-9](?:[a-z0-9-]{0,38})$")


def validate(sig: Signature, options: Options) -> list[str]:
    problems = []
    if not _ID.match(sig.id):
        problems.append(f"id «{sig.id}» должен выглядеть как gh-12, ya-12345, f-3a9c или m-1")
    try:
        date.fromisoformat(sig.date)
    except ValueError:
        problems.append(f"дата «{sig.date}» должна быть в виде ГГГГ-ММ-ДД")
    name, error = normalize_name(sig.name)
    if error:
        problems.append(f"подпись «{sig.name}»: {error}")
    faculty = options.faculty_by_name(sig.faculty)
    if faculty is None or faculty.other:
        _, unit_error = normalize_unit(sig.faculty)
        if faculty is not None or unit_error:
            problems.append(f"факультет «{sig.faculty}» не найден в options.toml")
    if options.status_by_short(sig.status) is None:
        problems.append(f"статус «{sig.status}» не найден в options.toml")
    if sig.source not in SOURCES:
        problems.append(f"source «{sig.source}» должен быть одним из: {', '.join(SOURCES)}")
    if sig.account and not _ACCOUNT.match(sig.account):
        problems.append(f"account «{sig.account}» должен выглядеть как gh:login")
    return problems


def load(path: Path, options: Options | None = None) -> list[Signature]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise SignatureError(f"{path.name}: первая строка должна быть «{','.join(FIELDS)}»")
        sigs = []
        for lineno, row in enumerate(reader, start=2):
            if None in row or any(v is None for v in row.values()):
                raise SignatureError(f"{path.name}, строка {lineno}: неверное число столбцов")
            sig = Signature(**{k: row[k].strip() for k in FIELDS})
            if options is not None:
                problems = validate(sig, options)
                if problems:
                    raise SignatureError(f"{path.name}, строка {lineno}: " + "; ".join(problems))
            sigs.append(sig)
    seen_ids, seen_accounts = set(), set()
    for sig in sigs:
        if sig.id in seen_ids:
            raise SignatureError(f"{path.name}: id {sig.id} встречается дважды")
        seen_ids.add(sig.id)
        if sig.account:
            if sig.account in seen_accounts:
                raise SignatureError(f"{path.name}: учётная запись {sig.account} подписала дважды")
            seen_accounts.add(sig.account)
    return sigs


def save(path: Path, sigs: list[Signature]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for sig in sigs:
            writer.writerow(asdict(sig))
    tmp.replace(path)


# ------------------------------------------------------ разбор заявок

_SECTION = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.M)


def parse_issue_form(body: str) -> dict[str, str]:
    """Тело заявки из формы GitHub: «### Поле» и значение под ним -> {ключ: значение}."""
    parts = _SECTION.split(body or "")
    by_label = {label: key for key, label in FORM_LABELS.items()}
    fields = {}
    for label, content in zip(parts[1::2], parts[2::2]):
        key = by_label.get(label.strip())
        if key:
            value = content.strip()
            fields[key] = "" if value == "_No response_" else value
    return fields


def parse_key_values(body: str) -> dict[str, str]:
    """Тело заявки от интеграции формы: строки «Поле: значение»."""
    fields = {}
    for line in (body or "").splitlines():
        m = re.match(r"^\s*([^:]{1,40}?)\s*:\s*(.*?)\s*$", line)
        if m:
            key = KEY_SYNONYMS.get(_norm(m.group(1)))
            if key and key not in fields:
                fields[key] = m.group(2)
    return fields


def consent_given(text: str) -> bool:
    """Все галочки в поле «Согласие» отмечены (и хотя бы одна есть)."""
    boxes = re.findall(r"^\s*[-*]\s*\[([ xX])\]", text or "", re.M)
    return bool(boxes) and all(box.lower() == "x" for box in boxes)


@dataclass
class Submission:
    name: str
    faculty: str
    status: str
    form_id: str = ""


def read_submission(
    fields: dict[str, str], options: Options, require_consent: bool = True
) -> tuple[Submission | None, list[str]]:
    """Проверить поля заявки. Возвращает (подпись, []) или (None, [ошибки])."""
    errors = []
    name, error = normalize_name(fields.get("name", ""))
    if error:
        errors.append(error)

    faculty_raw = (fields.get("faculty") or "").strip()
    faculty = options.match_faculty(faculty_raw)
    faculty_value = None
    if not faculty_raw:
        errors.append("не выбран факультет")
    elif faculty is None:
        errors.append(f"факультет «{faculty_raw}» не найден в списке")
    elif faculty.other:
        faculty_value, error = normalize_unit(fields.get("faculty_other", ""))
        if error:
            errors.append(error)
    else:
        faculty_value = faculty.name

    status = options.match_status(fields.get("status", ""))
    if status is None:
        raw = (fields.get("status") or "").strip()
        errors.append(f"курс или статус «{raw}» не распознан" if raw else "не указан курс или статус")

    if require_consent and not consent_given(fields.get("consent", "")):
        errors.append("не отмечены обе галочки согласия")

    form_id = re.sub(r"[^\w\-]", "", fields.get("form_id", ""))[:64]
    if errors:
        return None, errors
    return Submission(name, faculty_value, status.short, form_id), []
