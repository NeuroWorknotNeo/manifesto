#!/usr/bin/env python3
"""Перенести подписи из заявок (issues) на GitHub в signatures.csv.

Запускается из GitHub Actions (.github/workflows/signatures.yml) на каждую
новую заявку и раз в час на всякий случай. Берёт все открытые заявки,
похожие на подпись, проверяет поля, дописывает подписи в signatures.csv,
делает коммит и push, запускает пересборку сайта, отвечает в заявках и
закрывает их. Повторный запуск ничего не ломает: заявка, чья подпись уже
в списке, просто закрывается.

Переменные окружения:
  GITHUB_TOKEN       токен с правами contents, issues и actions на запись
  GITHUB_REPOSITORY  владелец/репозиторий
  FORM_BOT_LOGIN     необязательно: учётная запись, от имени которой заявки
                     создаёт интеграция Яндекс Формы (см. README)
  DRY_RUN=1          ничего не менять, только показать решения
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build  # noqa: E402
import signatures as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "signatures.csv"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
MARKER = "<!-- manifesto-bot -->"
LABEL = "подпись"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo

    def request(self, method: str, path: str, data: dict | None = None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "msu-ai-manifesto-bot",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            raise GitHubError(f"{method} {path}: {err.code} {detail}") from None

    def default_branch(self) -> str:
        return self.request("GET", f"/repos/{self.repo}")[1]["default_branch"]

    def open_issues(self) -> list[dict]:
        issues, page = [], 1
        while True:
            _, batch = self.request(
                "GET",
                f"/repos/{self.repo}/issues?state=open&per_page=100&page={page}&sort=created&direction=asc",
            )
            issues += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return issues
            page += 1

    def ensure_label(self) -> None:
        try:
            self.request(
                "POST",
                f"/repos/{self.repo}/labels",
                {"name": LABEL, "color": "1b365d", "description": "Подпись под манифестом"},
            )
        except GitHubError as err:
            if " 422 " not in str(err):  # 422 — метка уже есть
                raise

    def comments(self, number: int) -> list[dict]:
        return self.request("GET", f"/repos/{self.repo}/issues/{number}/comments?per_page=100")[1]

    def comment(self, number: int, text: str) -> None:
        self.request("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": text})

    def close(self, number: int, reason: str) -> None:
        self.request(
            "PATCH", f"/repos/{self.repo}/issues/{number}", {"state": "closed", "state_reason": reason}
        )

    def dispatch(self, workflow: str, ref: str) -> None:
        self.request(
            "POST", f"/repos/{self.repo}/actions/workflows/{workflow}/dispatches", {"ref": ref}
        )


# ------------------------------------------------------------- решения


@dataclass
class Decision:
    issue: int
    kind: str  # accept | already | duplicate | reject
    from_form: bool = False
    signature: sg.Signature | None = None
    reasons: list[str] = field(default_factory=list)
    existing_id: str = ""  # для already и duplicate: какая подпись уже есть


def decide(issues: list[dict], sigs: list[sg.Signature], options: sg.Options, form_bot: str = "") -> list[Decision]:
    """Что делать с каждой заявкой. Не трогает сеть и файлы — удобно проверять тестами."""
    form_bot = form_bot.strip().lower()
    ids = {s.id for s in sigs}
    accounts = {s.account: s.id for s in sigs if s.account}
    decisions = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        author = ((issue.get("user") or {}).get("login") or "").lower()
        body = issue.get("body") or ""
        from_form = False
        fields = sg.parse_issue_form(body)
        if form_bot and author == form_bot and not {"name", "faculty"} <= fields.keys():
            # заявку создала интеграция формы: строки «Поле: значение»
            fields = sg.parse_key_values(body)
            from_form = True
        if not {"name", "faculty"} <= fields.keys():
            continue  # обычная заявка, не подпись
        number = issue["number"]
        issue_id = f"gh-{number}"
        if issue_id in ids:
            decisions.append(Decision(number, "already", from_form, existing_id=issue_id))
            continue
        sub, errors = sg.read_submission(fields, options, require_consent=not from_form)
        if errors:
            decisions.append(Decision(number, "reject", from_form, reasons=errors))
            continue
        if from_form:
            sig_id = f"ya-{sub.form_id}" if sub.form_id else issue_id
            account = ""
            if sig_id in ids:
                decisions.append(Decision(number, "already", True, existing_id=sig_id))
                continue
        else:
            sig_id = issue_id
            account = f"gh:{author}"
            if account in accounts:
                decisions.append(Decision(number, "duplicate", existing_id=accounts[account]))
                continue
        sig = sg.Signature(
            id=sig_id,
            date=sg.moscow_date(issue["created_at"]),
            name=sub.name,
            faculty=sub.faculty,
            status=sub.status,
            source="form" if from_form else "github",
            account=account,
        )
        ids.add(sig_id)
        if account:
            accounts[account] = sig_id
        decisions.append(Decision(number, "accept", from_form, signature=sig))
    return decisions


def group_numbers(sigs: list[sg.Signature], options: sg.Options) -> dict[str, int]:
    """Номер каждой подписи в её списке — так же, как их нумерует tools/build.py."""
    ordered = sorted(enumerate(sigs), key=lambda pair: (pair[1].date, pair[0]))
    counters: dict[str, int] = {}
    numbers = {}
    for _, sig in ordered:
        group = options.group_of(sig.status)
        counters[group] = counters.get(group, 0) + 1
        numbers[sig.id] = counters[group]
    return numbers


def reply_text(d: Decision, sigs: list[sg.Signature], options: sg.Options, project: build.Project) -> str | None:
    """Текст ответа в заявке; None — отвечать не нужно."""
    by_id = {s.id: s for s in sigs}
    numbers = group_numbers(sigs, options)
    if d.kind in ("accept", "already"):
        if d.from_form:
            return None
        sig = d.signature or by_id.get(d.existing_id)
        if sig is None:
            return None
        detail = f"{options.faculty_short(sig.faculty)}, {sig.status}"
        return (
            f"Спасибо! Подпись **{sig.name}** ({detail}) добавлена в список под номером "
            f"{numbers[sig.id]}.\n\n"
            f"Сайт и PDF обновятся через пару минут: {project.site_url}\n\n"
            "Чтобы отозвать подпись, напишите об этом в комментарии к этой заявке.\n\n" + MARKER
        )
    if d.kind == "duplicate":
        number = numbers.get(d.existing_id)
        suffix = f" (номер {number} в списке)" if number else ""
        return (
            f"С этой учётной записи GitHub манифест уже подписан{suffix}. Спасибо!\n\n"
            "Если нужно исправить подпись, напишите об этом в комментарии.\n\n" + MARKER
        )
    if d.kind == "reject":
        reasons = "\n".join(f"- {r}" for r in d.reasons)
        return (
            f"Не получилось добавить подпись:\n\n{reasons}\n\n"
            f"Пожалуйста, заполните форму ещё раз: {project.github_sign_url}\n\n" + MARKER
        )
    return None


# ------------------------------------------------------------------ git


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=check, capture_output=True, text=True)


def commit_and_push(message: str, branch: str) -> bool:
    git("add", str(CSV))
    git(
        "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", message,
    )
    pushed = git("push", "origin", f"HEAD:{branch}", check=False)
    if pushed.returncode != 0:
        print(f"push не прошёл: {pushed.stderr.strip()}", file=sys.stderr)
    return pushed.returncode == 0


def reset_to_remote(branch: str) -> None:
    git("fetch", "origin", branch)
    git("reset", "--hard", f"origin/{branch}")


# ------------------------------------------------------------------ main


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise SystemExit("нужны переменные окружения GITHUB_TOKEN и GITHUB_REPOSITORY")
    dry_run = os.environ.get("DRY_RUN") == "1"
    form_bot = os.environ.get("FORM_BOT_LOGIN", "")
    gh = GitHub(token, repo)
    options = sg.Options.load(ROOT / "options.toml")
    project = build.load_project()
    branch = gh.default_branch()
    if not dry_run:
        gh.ensure_label()
        # работаем строго поверх основной ветки: при ручном запуске с другой
        # ветки её коммиты не должны уехать в main вместе с подписями
        reset_to_remote(branch)

    for attempt in range(3):
        sigs = sg.load(CSV, options)
        decisions = decide(gh.open_issues(), sigs, options, form_bot)
        accepted = [d for d in decisions if d.kind == "accept"]
        for d in decisions:
            what = d.signature.name if d.signature else "; ".join(d.reasons) or d.existing_id
            print(f"#{d.issue}: {d.kind} {what}")
        if dry_run:
            return
        if not accepted:
            break
        sigs = sigs + [d.signature for d in accepted]
        sg.save(CSV, sigs)
        refs = ", ".join(f"#{d.issue}" for d in accepted)
        if commit_and_push(f"Подписи: +{len(accepted)} ({refs})", branch):
            gh.dispatch("publish.yml", branch)
            break
        reset_to_remote(branch)  # кто-то успел запушить раньше — начинаем заново
    else:
        raise SystemExit("не удалось отправить подписи после трёх попыток; следующий запуск попробует снова")

    failures = 0
    for d in decisions:
        try:
            text = reply_text(d, sigs, options, project)
            if text and not (
                d.kind == "already" and any(MARKER in (c.get("body") or "") for c in gh.comments(d.issue))
            ):
                gh.comment(d.issue, text)
            gh.close(d.issue, "completed" if d.kind in ("accept", "already") else "not_planned")
        except GitHubError as err:
            failures += 1
            print(f"#{d.issue}: не удалось ответить или закрыть: {err}", file=sys.stderr)
    if failures:
        print(f"не закрыто заявок: {failures}; их подберёт следующий запуск", file=sys.stderr)


if __name__ == "__main__":
    main()
