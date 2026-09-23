#!/usr/bin/env python3
"""Сборка манифеста: PDF (через Typst) и статический сайт для GitHub Pages.

    python3 tools/build.py              собрать build/manifesto.pdf и сайт в build/site/
    python3 tools/build.py --snapshot   то же и положить в manifesto.pdf текст без списка подписей
    python3 tools/build.py issue-form   пересоздать форму подписи в .github/ISSUE_TEMPLATE/

Нужны Python 3.11+ и пакеты из requirements.txt: pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mdlite  # noqa: E402
import signatures as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def human_date(d: date) -> str:
    return f"{d.day}{mdlite.NBSP}{MONTHS[d.month - 1]} {d.year}{mdlite.NBSP}г."


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def number(n: int) -> str:
    """12345 -> «12 345» с неразрывным пробелом."""
    return f"{n:,}".replace(",", mdlite.NBSP)


def warn(message: str) -> None:
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "Предупреждение: "
    print(prefix + message, file=sys.stderr)


# ------------------------------------------------------------ настройки


@dataclass
class Project:
    repo: str
    site_url: str
    github_sign_url: str
    form_url: str
    edition: str
    edition_date: date
    organizer: str
    contact: str

    @property
    def sign_url(self) -> str:
        return self.form_url or self.github_sign_url

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"


def load_project(use_env: bool = True) -> Project:
    with open(ROOT / "config.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    config_repo = cfg.get("github_repo", "").strip()
    env_repo = os.environ.get("GITHUB_REPOSITORY", "").strip() if use_env else ""
    if env_repo and config_repo and env_repo.lower() != config_repo.lower():
        warn(
            f"в config.toml github_repo = {config_repo}, а сборка идёт в {env_repo}. "
            "Поправьте config.toml и выполните python3 tools/build.py issue-form, "
            "иначе ссылки в форме подписи будут вести не туда."
        )
    repo = env_repo or config_repo
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise SystemExit("config.toml: github_repo должен выглядеть как «владелец/репозиторий»")
    owner, name = repo.split("/")
    site_url = cfg.get("site_url", "").strip() or f"https://{owner.lower()}.github.io/{name}/"
    if not site_url.endswith("/"):
        site_url += "/"
    return Project(
        repo=repo,
        site_url=site_url,
        github_sign_url=f"https://github.com/{repo}/issues/new?template=podpis.yml",
        form_url=cfg.get("form_url", "").strip(),
        edition=str(cfg.get("edition", "1.0")),
        edition_date=date.fromisoformat(str(cfg.get("edition_date"))),
        organizer=cfg.get("organizer", "").strip(),
        contact=cfg.get("contact", "").strip(),
    )


def display_url(url: str) -> str:
    return re.sub(r"^https?://", "", url).rstrip("/")


# ---------------------------------------------------------------- тексты


@dataclass
class Texts:
    manifesto: mdlite.Document
    consent: mdlite.Document


def _md_escape(value: str) -> str:
    return re.sub(r"([\\`*\[\]<>])", r"\\\1", value)


def load_texts(project: Project) -> Texts:
    def read(name: str) -> str:
        return (ROOT / "text" / name).read_text(encoding="utf-8")

    organizer = project.organizer or "организаторы инициативы"
    contact = project.contact or "контакт будет указан на сайте"
    if not project.organizer or not project.contact:
        warn("в config.toml не заполнены organizer и contact — их нужно указать до начала сбора подписей")
    consent_src = read("consent.md").replace("{{organizer}}", _md_escape(organizer))
    consent_src = consent_src.replace("{{contact}}", _md_escape(contact))
    texts = Texts(
        manifesto=mdlite.parse(read("manifesto.md")),
        consent=mdlite.parse(consent_src),
    )
    if texts.manifesto.title is None:
        raise SystemExit("text/manifesto.md должен начинаться с заголовка «# Название»")
    return texts


# ----------------------------------------------------------------- данные


def assemble(project: Project, options: sg.Options, sigs: list[sg.Signature], today: date) -> dict:
    """Всё, что нужно шаблонам PDF и сайта, одним словарём."""
    ordered = sorted(enumerate(sigs), key=lambda pair: (pair[1].date, pair[0]))
    groups: dict[str, list[dict]] = {"students": [], "supporters": []}
    counts: dict[str, int] = {}
    for _, sig in ordered:
        group = groups[options.group_of(sig.status)]
        short = options.faculty_short(sig.faculty)
        group.append(
            {
                "n": len(group) + 1,
                "name": sig.name,
                "faculty": short,
                "status": sig.status,
                "detail": f"{short}, {sig.status}",
                "date": date.fromisoformat(sig.date).strftime("%d.%m.%Y"),
            }
        )
        counts[short] = counts.get(short, 0) + 1
    by_faculty = [
        {"faculty": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    ]
    total = len(sigs)
    students, supporters = len(groups["students"]), len(groups["supporters"])
    if total:
        summary = (
            f"Студенты и аспиранты — {number(students)}; "
            f"преподаватели, сотрудники и выпускники — {number(supporters)}; "
            f"факультетов и подразделений — {number(len(counts))}."
        )
    else:
        summary = "Подписей пока нет — ваша может стать первой."
    return {
        "updated": human_date(today),
        "edition": project.edition,
        "edition_date": human_date(project.edition_date),
        "site_url": project.site_url,
        "site_url_display": display_url(project.site_url),
        "sign_url": project.sign_url,
        "form_url": project.form_url,
        "github_sign_url": project.github_sign_url,
        "total": total,
        "total_label": f"{number(total)} {plural(total, 'подпись', 'подписи', 'подписей')}",
        "students_total": students,
        "supporters_total": supporters,
        "faculties_total": len(counts),
        "summary": summary,
        "organizer": project.organizer,
        "contact": project.contact,
        "by_faculty": by_faculty,
        "students": groups["students"],
        "supporters": groups["supporters"],
    }


# ------------------------------------------------------------------- PDF


def write_qr(url: str, path: Path) -> None:
    import segno

    segno.make(url, error="m").save(
        str(path), kind="svg", scale=4, border=0, dark="#1b365d", light=None, xmldecl=False
    )


def split_lead(doc: mdlite.Document) -> tuple[mdlite.Paragraph, list]:
    """Вводный абзац (первый после подзаголовка) и остальной текст."""
    if not doc.blocks or not isinstance(doc.blocks[0], mdlite.Paragraph):
        raise SystemExit("text/manifesto.md: после заголовка и подзаголовка должен идти вводный абзац")
    return doc.blocks[0], doc.blocks[1:]


def build_pdf(texts: Texts, data: dict, out: Path) -> Path:
    writer = mdlite.TypstWriter(texts.manifesto.footnotes)
    lead, body = split_lead(texts.manifesto)
    (out / "lead.typ").write_text(writer.inline(lead.children) + "\n", encoding="utf-8")
    (out / "body.typ").write_text(writer.blocks(body), encoding="utf-8")
    meta = dict(
        data,
        title=mdlite.plain_text(texts.manifesto.title),
        subtitle=mdlite.plain_text(texts.manifesto.subtitle),
    )
    (out / "data.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    write_qr(data["site_url"], out / "qr.svg")
    pdf = out / "manifesto.pdf"
    compile_pdf(out, pdf)
    return pdf


def compile_pdf(out: Path, pdf: Path, text_only: bool = False) -> None:
    """Собрать PDF из подготовленных в out файлов; text_only — без подписей."""
    import typst

    typst.compile(
        str(ROOT / "manifesto.typ"),
        output=str(pdf),
        root=str(ROOT),
        font_paths=[str(ROOT / "fonts")],
        ignore_system_fonts=True,
        sys_inputs={"build": out.relative_to(ROOT).as_posix(), "text_only": "1" if text_only else ""},
    )


# ------------------------------------------------------------------ сайт


def fill(template: str, values: dict) -> str:
    """Подставить {{ имя }}; неизвестное имя — ошибка, чтобы опечатки не уходили на сайт."""

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"в шаблоне есть {{{{ {key} }}}}, но такого значения нет")
        return str(values[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", repl, template)


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def _signature_list(entries: list[dict]) -> str:
    if not entries:
        return ""
    items = []
    for e in entries:
        search = f"{e['name']} {e['faculty']} {e['status']}".casefold().replace("ё", "е")
        items.append(
            f'<li data-search="{esc(search)}"><span class="n">{e["n"]}</span>'
            f'<span><span class="who">{esc(e["name"])}</span> '
            f'<span class="meta">— {esc(e["detail"])}</span></span></li>'
        )
    return '<ol class="sig-list">' + "".join(items) + "</ol>"


def _faculty_bars(by_faculty: list[dict]) -> str:
    if not by_faculty:
        return ""
    top = by_faculty[:12]
    peak = top[0]["count"]
    rows = []
    for row in top:
        width = max(4, round(100 * row["count"] / peak))
        rows.append(
            f'<li><span class="bar-label">{esc(row["faculty"])}</span>'
            f'<span class="bar" style="--w:{width}%"></span>'
            f'<span class="bar-count">{row["count"]}</span></li>'
        )
    rest = len(by_faculty) - len(top)
    more = f'<p class="bars-more">и ещё {rest} {plural(rest, "подразделение", "подразделения", "подразделений")}</p>' if rest > 0 else ""
    return '<ol class="bars">' + "".join(rows) + "</ol>" + more


def _sign_links(project: Project, primary_class: str = "btn primary") -> str:
    links = [f'<a class="{primary_class}" href="{esc(project.sign_url)}">Подписать манифест</a>']
    links.append('<a class="btn" href="manifesto.pdf">Скачать PDF</a>')
    return "".join(links)


def _alt_sign(project: Project) -> str:
    if project.form_url:
        return (
            '<p class="alt-sign">Есть аккаунт на GitHub? '
            f'<a href="{esc(project.github_sign_url)}">Подпишите через GitHub</a> — подпись появится через пару минут.</p>'
        )
    return '<p class="alt-sign">Подпись оставляется через GitHub и появляется в списке через пару минут.</p>'


def build_site(project: Project, texts: Texts, data: dict, pdf: Path, out: Path, signatures_csv: Path) -> None:
    site = out / "site"
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    css = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "site" / "app.js").read_text(encoding="utf-8")

    writer = mdlite.HtmlWriter()
    writer.add_footnotes(texts.manifesto.footnotes)
    lead, body = split_lead(texts.manifesto)
    lead_html = writer.inline(lead.children)
    body_html = writer.blocks(body)
    notes_html = writer.footnotes_html()

    title = mdlite.plain_text(texts.manifesto.title)
    description = mdlite.plain_text(lead.children).replace(mdlite.NBSP, " ")
    if len(description) > 220:
        description = description[:217].rsplit(" ", 1)[0] + "…"
    share_text = quote(title.replace(mdlite.NBSP, " "))
    share_url = quote(project.site_url, safe="")

    common = {
        "css": css,
        "js": js,
        "title": esc(title),
        "site_url": esc(project.site_url),
        "repo_url": esc(project.repo_url),
        "updated": esc(data["updated"]),
        "edition": esc(data["edition"]),
        "edition_date": esc(data["edition_date"]),
    }
    index = fill(
        (ROOT / "site" / "index.html").read_text(encoding="utf-8"),
        dict(
            common,
            subtitle=esc(mdlite.plain_text(texts.manifesto.subtitle)),
            description=esc(description),
            sign_links=_sign_links(project),
            alt_sign=_alt_sign(project),
            sign_url=esc(project.sign_url),
            total_label=esc(data["total_label"]),
            summary=(
                f"<strong>{esc(data['total_label'])}</strong> на {esc(data['updated'])} {esc(data['summary'])}"
                if data["total"]
                else esc(data["summary"])
            ),
            contact_line=(
                f"<p>Организатор сбора подписей: {esc(project.organizer)}. "
                f"Связаться: {esc(project.contact)}.</p>"
                if project.organizer and project.contact
                else ""
            ),
            lead=lead_html,
            body=body_html,
            notes=notes_html,
            faculty_bars=_faculty_bars(data["by_faculty"]),
            students=_signature_list(data["students"]),
            supporters=_signature_list(data["supporters"]),
            students_hidden="" if data["students"] else " hidden",
            supporters_hidden="" if data["supporters"] else " hidden",
            search_hidden="" if data["total"] >= 10 else " hidden",
            share_telegram=esc(f"https://t.me/share/url?url={share_url}&text={share_text}"),
            share_vk=esc(f"https://vk.com/share.php?url={share_url}"),
        ),
    )
    (site / "index.html").write_text(index, encoding="utf-8")

    consent_writer = mdlite.HtmlWriter()
    consent_writer.add_footnotes(texts.consent.footnotes)
    consent = fill(
        (ROOT / "site" / "consent.html").read_text(encoding="utf-8"),
        dict(
            common,
            page_title=esc(mdlite.plain_text(texts.consent.title)),
            page_subtitle=esc(mdlite.plain_text(texts.consent.subtitle)),
            content=consent_writer.blocks(texts.consent.blocks),
        ),
    )
    (site / "consent.html").write_text(consent, encoding="utf-8")

    shutil.copy(pdf, site / "manifesto.pdf")
    shutil.copy(signatures_csv, site / "signatures.csv")
    shutil.copy(out / "qr.svg", site / "qr.svg")
    (site / ".nojekyll").write_text("", encoding="utf-8")


# ---------------------------------------------------------- форма GitHub


def _yq(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def issue_form(project: Project, options: sg.Options) -> dict[str, str]:
    """Файлы формы подписи для .github/ISSUE_TEMPLATE/."""
    L = sg.FORM_LABELS
    intro = [
        "Спасибо, что поддерживаете предложение! "
        f"Текст манифеста и список подписей: {project.site_url}",
        "",
        "**Всё, что вы введёте в этой форме, будет опубликовано** — в этой заявке, "
        "в списке подписей на сайте и в PDF. Подробнее: "
        f"[согласие на публикацию подписи]({project.site_url}consent.html).",
        "",
        "Подпись добавится автоматически через пару минут: робот ответит в этой заявке и закроет её. "
        "С одной учётной записи GitHub можно подписать один раз.",
    ]
    faculties = "\n".join(f"        - {_yq(f.name)}" for f in options.faculties)
    statuses = "\n".join(f"        - {_yq(s.label)}" for s in options.statuses)
    intro_block = "\n".join(("        " + line) if line else "" for line in intro)
    form = f"""# Файл создан командой «python3 tools/build.py issue-form» из config.toml и
# options.toml. Не правьте его руками: поправьте исходники и выполните команду.
name: Подписать манифест
description: Поставить подпись под манифестом «Искусственный интеллект — каждому студенту МГУ»
title: Подпись
labels: ["подпись"]
body:
  - type: markdown
    attributes:
      value: |
{intro_block}
  - type: input
    id: name
    attributes:
      label: {_yq(L["name"])}
      description: "Так подпись будет выглядеть в списке. Кириллицей или латиницей; можно сокращённо — «Иван И.»."
      placeholder: Иван Иванов
    validations:
      required: true
  - type: dropdown
    id: faculty
    attributes:
      label: {_yq(L["faculty"])}
      options:
{faculties}
    validations:
      required: true
  - type: input
    id: faculty_other
    attributes:
      label: {_yq(L["faculty_other"])}
      description: {_yq("Заполните, только если выбрали «" + options.other.name + "».")}
      placeholder: Например, филиал МГУ в Севастополе
  - type: dropdown
    id: status
    attributes:
      label: {_yq(L["status"])}
      options:
{statuses}
    validations:
      required: true
  - type: checkboxes
    id: consent
    attributes:
      label: {_yq(L["consent"])}
      options:
        - label: "Я учусь или работаю в МГУ либо окончил(а) его и поддерживаю предложение манифеста."
          required: true
        - label: "Я согласен(на) на публикацию имени, факультета, курса и даты подписи в открытом списке подписавших."
          required: true
"""
    config = f"""# Файл создан командой «python3 tools/build.py issue-form».
blank_issues_enabled: true
contact_links:
  - name: Текст манифеста и список подписей
    url: {project.site_url}
    about: Сайт инициативы — там же PDF и подпись без GitHub, если она настроена.
"""
    return {"podpis.yml": form, "config.yml": config}


def write_issue_form() -> None:
    project = load_project(use_env=False)
    options = sg.Options.load(ROOT / "options.toml")
    folder = ROOT / ".github" / "ISSUE_TEMPLATE"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in issue_form(project, options).items():
        (folder / name).write_text(content, encoding="utf-8")
        print(f"записан {folder.relative_to(ROOT) / name}")


# ---------------------------------------------------------------- main


def build(
    out: Path | None = None,
    today: date | None = None,
    signatures_csv: Path | None = None,
    text_only_pdf: bool = False,
) -> Path:
    """Собрать PDF и сайт. out — папка внутри проекта (Typst не видит файлов вне него).
    text_only_pdf — ещё и out/manifesto-text.pdf: текст без счётчика и списка подписей."""
    out = (out or ROOT / "build").resolve()
    out.mkdir(parents=True, exist_ok=True)
    today = today or datetime.now(sg.MOSCOW).date()
    signatures_csv = signatures_csv or ROOT / "signatures.csv"
    project = load_project()
    options = sg.Options.load(ROOT / "options.toml")
    try:
        sigs = sg.load(signatures_csv, options)
    except sg.SignatureError as err:
        raise SystemExit(f"Ошибка в списке подписей: {err}")
    texts = load_texts(project)
    data = assemble(project, options, sigs, today)
    pdf = build_pdf(texts, data, out)
    build_site(project, texts, data, pdf, out, signatures_csv)
    if text_only_pdf:
        compile_pdf(out, out / "manifesto-text.pdf", text_only=True)
    return pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=["build", "issue-form"], default="build")
    parser.add_argument("--snapshot", action="store_true", help="положить в manifesto.pdf текст без списка подписей")
    parser.add_argument("--signatures", type=Path, help="взять подписи из другого CSV (для проверки вёрстки)")
    parser.add_argument("--out", type=Path, help="папка для результата внутри проекта (по умолчанию build/)")
    args = parser.parse_args()
    if args.command == "issue-form":
        write_issue_form()
        return
    pdf = build(out=args.out, signatures_csv=args.signatures, text_only_pdf=args.snapshot)
    print(f"PDF: {pdf.relative_to(ROOT)}; сайт: {(pdf.parent / 'site' / 'index.html').relative_to(ROOT)}")
    if args.snapshot:
        shutil.copy(pdf.parent / "manifesto-text.pdf", ROOT / "manifesto.pdf")
        print("manifesto.pdf: текст без списка подписей")


if __name__ == "__main__":
    main()
