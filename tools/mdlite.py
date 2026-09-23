"""Маленький конвертер Markdown -> Typst и HTML.

Поддерживается ровно то подмножество Markdown, которое нужно текстам манифеста:

* заголовки ``#``, ``##``, ``###``; первый заголовок ``#`` — название документа,
  а абзац сразу после него, целиком набранный курсивом, — подзаголовок;
* абзацы, ``**полужирный**``, ``*курсив*``, ```код```;
* ссылки ``[текст](адрес)`` и ``<адрес>``;
* сноски ``[^метка]`` с определениями ``[^метка]: текст`` в любом месте файла;
* маркированные (``-``) и нумерованные (``1.``) списки в один уровень;
* таблицы с вертикальными чертами, цитаты (``>``), черта ``---``.

Всё остальное считается обычным текстом и выводится как есть, без сюрпризов:
в Typst каждый кусок текста уходит строковым литералом, поэтому знаки вроде
``#``, ``$`` или ``//`` в тексте ничего не ломают.

Заодно делается простая русская типографика: неразрывные пробелы после
коротких слов, перед тире, между инициалами и внутри чисел.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

NBSP = "\u00a0"
WORD_JOINER = "\u2060"


class MarkdownError(ValueError):
    """Текст не удалось разобрать: сообщение объясняет, что поправить."""


# ---------------------------------------------------------------- дерево


@dataclass
class Text:
    value: str


@dataclass
class Strong:
    children: list


@dataclass
class Emph:
    children: list


@dataclass
class Code:
    value: str


@dataclass
class Link:
    url: str
    children: list


@dataclass
class FootnoteRef:
    key: str


@dataclass
class Heading:
    level: int
    children: list


@dataclass
class Paragraph:
    children: list


@dataclass
class ListBlock:
    ordered: bool
    start: int
    items: list  # список строчных элементов на каждый пункт


@dataclass
class Table:
    aligns: list  # "left" | "center" | "right" на каждый столбец
    header: list  # ячейки заголовка
    rows: list  # строки из ячеек


@dataclass
class Quote:
    blocks: list


@dataclass
class Rule:
    pass


@dataclass
class Document:
    title: list | None
    subtitle: list | None
    blocks: list
    footnotes: dict = field(default_factory=dict)


# ------------------------------------------------------------ типографика

# предлоги, союзы и частицы из одной-двух букв приклеиваются к следующему слову
_SHORT_WORD = re.compile(r"(?<![\w\-])([А-Яа-яЁё]{1,2}) (?=\S)")
# частицы, которые приклеиваются к предыдущему слову
_PARTICLE = re.compile(r" (ли|же|бы|ль|ж|б)(?=[\s.,;:!?»)]|$)")
_INITIAL = re.compile(r"(?<![\w.])([А-ЯЁA-Z])\. (?=[А-ЯЁA-Z])")
_NUMBER_WORD = re.compile(r"(\d) (?=[А-Яа-яЁё%‰])")
_DIGIT_GROUP = re.compile(r"(?<=\d) (?=\d{3}(?!\d))")
_TRAILING_SHORT = re.compile(r"(?<![\w\-])[А-Яа-яЁё]{1,2} $")
_RANGE = re.compile(r"(?<=\d)–(?=\d)")


def typograph(text: str) -> str:
    """Расставить неразрывные пробелы в обычном тексте."""
    text = text.replace(" — ", NBSP + "— ")
    text = text.replace("№ ", "№" + NBSP)
    # «15–22» не разрывается между строками
    text = _RANGE.sub(WORD_JOINER + "–" + WORD_JOINER, text)
    text = _DIGIT_GROUP.sub(NBSP, text)
    text = _NUMBER_WORD.sub(r"\1" + NBSP, text)
    text = _INITIAL.sub(r"\1." + NBSP, text)
    text = _PARTICLE.sub(NBSP + r"\1", text)
    # два прохода: в «и в России» короткие слова идут подряд
    for _ in range(2):
        text = _SHORT_WORD.sub(r"\1" + NBSP, text)
    return text


def _typograph_inlines(nodes: list) -> list:
    out = []
    for i, node in enumerate(nodes):
        if isinstance(node, Text):
            value = typograph(node.value)
            # «с **помощью**»: короткое слово в конце куска приклеиваем к следующему
            if i + 1 < len(nodes) and _TRAILING_SHORT.search(value):
                value = value[:-1] + NBSP
            node = Text(value)
        elif isinstance(node, (Strong, Emph)):
            node = type(node)(_typograph_inlines(node.children))
        elif isinstance(node, Link):
            node = Link(node.url, _typograph_inlines(node.children))
        out.append(node)
    return out


# ------------------------------------------------------ строчная разметка

_ESCAPABLE = set("\\`*_{}[]()#+-.!|<>~\"'")
_AUTOLINK = re.compile(r"<(https?://[^\s<>]+)>")


def _find_closing(s: str, start: int, marker: str) -> int:
    """Позиция закрывающего маркера: перед ним не пробел, он не экранирован."""
    i = start
    while True:
        j = s.find(marker, i)
        if j == -1:
            return -1
        if s[j - 1] != "\\" and not s[j - 1].isspace():
            if marker != "*" or not s.startswith("**", j):
                return j
            i = j + 2
            continue
        i = j + len(marker)


def _find_bracket(s: str, start: int) -> int:
    """Закрывающая ``]`` для ``[`` в позиции start с учётом вложенности."""
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_paren(s: str, start: int) -> int:
    """Закрывающая ``)`` для адреса ссылки, начинающегося после ``(``."""
    depth = 1
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_inline(s: str) -> list:
    out: list = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(Text("".join(buf)))
            buf.clear()

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in _ESCAPABLE:
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "`":
            j = s.find("`", i + 1)
            if j != -1:
                flush()
                out.append(Code(s[i + 1:j]))
                i = j + 1
                continue
        if s.startswith("**", i) and i + 2 < n and not s[i + 2].isspace():
            j = _find_closing(s, i + 2, "**")
            if j != -1:
                flush()
                out.append(Strong(parse_inline(s[i + 2:j])))
                i = j + 2
                continue
        if c == "*" and not s.startswith("**", i) and i + 1 < n and not s[i + 1].isspace():
            j = _find_closing(s, i + 1, "*")
            if j != -1:
                flush()
                out.append(Emph(parse_inline(s[i + 1:j])))
                i = j + 1
                continue
        if s.startswith("[^", i):
            j = s.find("]", i)
            if j != -1 and re.fullmatch(r"[\w\-]+", s[i + 2:j]):
                flush()
                out.append(FootnoteRef(s[i + 2:j]))
                i = j + 1
                continue
        if c == "[":
            close = _find_bracket(s, i)
            if close != -1 and s.startswith("(", close + 1):
                end = _find_paren(s, close + 2)
                if end != -1:
                    flush()
                    url = s[close + 2:end].strip()
                    out.append(Link(url, parse_inline(s[i + 1:close])))
                    i = end + 1
                    continue
        if c == "<":
            m = _AUTOLINK.match(s, i)
            if m:
                flush()
                out.append(Link(m.group(1), [Text(m.group(1))]))
                i = m.end()
                continue
        buf.append(c)
        i += 1
    flush()
    return _typograph_inlines(out)


# ------------------------------------------------------------ блоки

_FOOTNOTE_DEF = re.compile(r"^\[\^([\w\-]+)\]:\s*(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s{0,3}(\d{1,9})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, buf, i = [], [], 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if line[i] == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(line[i])
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _aligns(sep_line: str) -> list[str]:
    aligns = []
    for cell in _split_row(sep_line):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left")
    return aligns


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and "-" in lines[i + 1]
        and bool(_TABLE_SEP.match(lines[i + 1]))
    )


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(
        _HEADING.match(line)
        or _RULE.match(line)
        or _QUOTE.match(line)
        or _BULLET.match(line)
        or _ORDERED.match(line)
        or _is_table_start(lines, i)
    )


def _parse_blocks(lines: list[str]) -> list:
    blocks: list = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = _HEADING.match(line)
        if m:
            blocks.append(Heading(len(m.group(1)), parse_inline(m.group(2))))
            i += 1
            continue

        if _RULE.match(line):
            blocks.append(Rule())
            i += 1
            continue

        if _QUOTE.match(line):
            inner = []
            while i < n and lines[i].strip():
                qm = _QUOTE.match(lines[i])
                inner.append(qm.group(1) if qm else lines[i])
                i += 1
            blocks.append(Quote(_parse_blocks(inner)))
            continue

        if _is_table_start(lines, i):
            header = _split_row(line)
            aligns = _aligns(lines[i + 1])
            if len(aligns) != len(header):
                raise MarkdownError(
                    f"таблица «{line.strip()}»: в строке-разделителе {len(aligns)} "
                    f"столбцов, а в заголовке {len(header)}"
                )
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                cells = _split_row(lines[i])
                if len(cells) != len(header):
                    raise MarkdownError(
                        f"строка таблицы «{lines[i].strip()}»: ячеек {len(cells)}, "
                        f"а столбцов {len(header)}"
                    )
                rows.append([parse_inline(c) for c in cells])
                i += 1
            blocks.append(Table(aligns, [parse_inline(c) for c in header], rows))
            continue

        bullet, ordered = _BULLET.match(line), _ORDERED.match(line)
        if bullet or ordered:
            pattern = _ORDERED if ordered else _BULLET
            start = int(ordered.group(1)) if ordered else 1
            items: list[list[str]] = []
            while i < n:
                cur = lines[i]
                im = pattern.match(cur)
                if im:
                    items.append([im.group(im.lastindex)])
                    i += 1
                    continue
                if cur.strip() and cur[:1] in (" ", "\t") and items:
                    items[-1].append(cur.strip())
                    i += 1
                    continue
                if not cur.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and pattern.match(lines[j]):
                        i = j
                        continue
                break
            blocks.append(
                ListBlock(bool(ordered), start, [parse_inline(" ".join(it)) for it in items])
            )
            continue

        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        blocks.append(Paragraph(parse_inline(" ".join(para))))
    return blocks


def parse(text: str) -> Document:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body: list[str] = []
    raw_notes: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = _FOOTNOTE_DEF.match(lines[i])
        if not m:
            body.append(lines[i])
            i += 1
            continue
        key, parts = m.group(1), [m.group(2)]
        i += 1
        while i < len(lines) and lines[i][:1] in (" ", "\t") and lines[i].strip():
            parts.append(lines[i].strip())
            i += 1
        if key in raw_notes:
            raise MarkdownError(f"сноска [^{key}] определена дважды")
        raw_notes[key] = " ".join(p for p in parts if p)

    blocks = _parse_blocks(body)
    title = subtitle = None
    if blocks and isinstance(blocks[0], Heading) and blocks[0].level == 1:
        title = blocks.pop(0).children
        first = blocks[0] if blocks else None
        if (
            isinstance(first, Paragraph)
            and len(first.children) == 1
            and isinstance(first.children[0], Emph)
        ):
            subtitle = blocks.pop(0).children[0].children
    notes = {key: parse_inline(value) for key, value in raw_notes.items()}
    doc = Document(title, subtitle, blocks, notes)
    _check_footnotes(doc)
    return doc


def _walk_inlines(nodes: list):
    for node in nodes:
        yield node
        if isinstance(node, (Strong, Emph, Link)):
            yield from _walk_inlines(node.children)


def _walk_doc_inlines(doc: Document):
    def blocks(bs):
        for b in bs:
            if isinstance(b, (Heading, Paragraph)):
                yield from _walk_inlines(b.children)
            elif isinstance(b, ListBlock):
                for item in b.items:
                    yield from _walk_inlines(item)
            elif isinstance(b, Table):
                for cell in b.header:
                    yield from _walk_inlines(cell)
                for row in b.rows:
                    for cell in row:
                        yield from _walk_inlines(cell)
            elif isinstance(b, Quote):
                yield from blocks(b.blocks)

    yield from blocks(doc.blocks)
    for part in (doc.title, doc.subtitle):
        if part:
            yield from _walk_inlines(part)


def _check_footnotes(doc: Document) -> None:
    used = [n.key for n in _walk_doc_inlines(doc) if isinstance(n, FootnoteRef)]
    missing = sorted(set(used) - set(doc.footnotes))
    if missing:
        raise MarkdownError("нет определения у сносок: " + ", ".join(f"[^{k}]" for k in missing))
    unused = sorted(set(doc.footnotes) - set(used))
    if unused:
        raise MarkdownError("сноски определены, но нигде не упомянуты: " + ", ".join(f"[^{k}]" for k in unused))
    for key, content in doc.footnotes.items():
        if any(isinstance(n, FootnoteRef) for n in _walk_inlines(content)):
            raise MarkdownError(f"сноска [^{key}] ссылается на другую сноску")


def plain_text(nodes: list | None) -> str:
    """Текст без разметки — для заголовка окна, метаданных PDF и т. п."""
    if not nodes:
        return ""
    parts = []
    for node in nodes:
        if isinstance(node, Text):
            parts.append(node.value)
        elif isinstance(node, Code):
            parts.append(node.value)
        elif isinstance(node, (Strong, Emph, Link)):
            parts.append(plain_text(node.children))
    return "".join(parts)


# ----------------------------------------------------------------- Typst


def typst_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class TypstWriter:
    """Переводит дерево в разметку Typst. Сноски печатаются там, где на них
    сослались; повторная ссылка на ту же сноску даёт тот же номер."""

    def __init__(self, footnotes: dict):
        self.footnotes = footnotes
        self.seen: set[str] = set()

    @staticmethod
    def _label(key: str) -> str:
        return "fn-" + re.sub(r"[^\w\-]", "-", key)

    def inline(self, nodes: list) -> str:
        parts = []
        for node in nodes:
            if isinstance(node, Text):
                parts.append("#" + typst_string(node.value))
            elif isinstance(node, Strong):
                parts.append("#strong[" + self.inline(node.children) + "]")
            elif isinstance(node, Emph):
                parts.append("#emph[" + self.inline(node.children) + "]")
            elif isinstance(node, Code):
                parts.append("#raw(" + typst_string(node.value) + ")")
            elif isinstance(node, Link):
                parts.append(f"#link({typst_string(node.url)})[" + self.inline(node.children) + "]")
            elif isinstance(node, FootnoteRef):
                label = self._label(node.key)
                if node.key in self.seen:
                    parts.append(f"#footnote(<{label}>)")
                else:
                    self.seen.add(node.key)
                    parts.append("#footnote[" + self.inline(self.footnotes[node.key]) + f"]<{label}>")
            else:  # pragma: no cover
                raise TypeError(node)
        return "".join(parts)

    def blocks(self, blocks: list) -> str:
        out = []
        for b in blocks:
            if isinstance(b, Heading):
                level = max(1, b.level - 1)
                out.append(f"#heading(level: {level})[" + self.inline(b.children) + "]")
            elif isinstance(b, Paragraph):
                out.append(self.inline(b.children))
            elif isinstance(b, ListBlock):
                items = ", ".join("[" + self.inline(item) + "]" for item in b.items)
                if b.ordered:
                    out.append(f"#enum(start: {b.start}, {items})")
                else:
                    out.append(f"#list({items})")
            elif isinstance(b, Table):
                cols = len(b.header)
                # первый столбец забирает свободное место, остальные — по содержимому
                columns = "(" + ", ".join(["1fr"] + ["auto"] * (cols - 1)) + ("," if cols == 1 else "") + ")"
                align = "(" + ", ".join(b.aligns) + ("," if cols == 1 else "") + ")"
                cells = [
                    "table.header(" + ", ".join("[" + self.inline(c) + "]" for c in b.header) + ")"
                ]
                for row in b.rows:
                    cells.extend("[" + self.inline(c) + "]" for c in row)
                out.append(f"#table(columns: {columns}, align: {align}, " + ", ".join(cells) + ")")
            elif isinstance(b, Quote):
                out.append("#quote(block: true)[\n" + self.blocks(b.blocks) + "]")
            elif isinstance(b, Rule):
                out.append('#align(center)[#text(fill: luma(140))[#"⁂"]]')
            else:  # pragma: no cover
                raise TypeError(b)
        return "\n\n".join(out) + "\n"


# ------------------------------------------------------------------ HTML


def _attr(s: str) -> str:
    return html.escape(s, quote=True)


class HtmlWriter:
    """Переводит дерево в HTML. Сноски нумеруются в порядке первого
    упоминания; несколько документов могут делить одну нумерацию."""

    def __init__(self) -> None:
        self.footnotes: dict = {}
        self.order: list[str] = []
        self.refs: dict[str, int] = {}

    def add_footnotes(self, footnotes: dict) -> None:
        clash = set(footnotes) & set(self.footnotes)
        if clash:
            raise MarkdownError("одинаковые метки сносок в разных файлах: " + ", ".join(sorted(clash)))
        self.footnotes.update(footnotes)

    @staticmethod
    def _id(key: str) -> str:
        return re.sub(r"[^\w\-]", "-", key)

    def inline(self, nodes: list) -> str:
        parts = []
        for node in nodes:
            if isinstance(node, Text):
                parts.append(html.escape(node.value, quote=False))
            elif isinstance(node, Strong):
                parts.append("<strong>" + self.inline(node.children) + "</strong>")
            elif isinstance(node, Emph):
                parts.append("<em>" + self.inline(node.children) + "</em>")
            elif isinstance(node, Code):
                parts.append("<code>" + html.escape(node.value, quote=False) + "</code>")
            elif isinstance(node, Link):
                external = node.url.startswith(("http://", "https://"))
                extra = ' rel="noopener"' if external else ""
                parts.append(f'<a href="{_attr(node.url)}"{extra}>' + self.inline(node.children) + "</a>")
            elif isinstance(node, FootnoteRef):
                if node.key not in self.order:
                    self.order.append(node.key)
                number = self.order.index(node.key) + 1
                self.refs[node.key] = self.refs.get(node.key, 0) + 1
                fid = self._id(node.key)
                ref_id = f"fnref-{fid}-{self.refs[node.key]}"
                parts.append(
                    f'<sup class="fnref"><a href="#fn-{fid}" id="{ref_id}" '
                    f'aria-label="Сноска {number}">{number}</a></sup>'
                )
            else:  # pragma: no cover
                raise TypeError(node)
        return "".join(parts)

    def blocks(self, blocks: list) -> str:
        out = []
        for b in blocks:
            if isinstance(b, Heading):
                level = min(max(b.level, 2), 6)
                out.append(f"<h{level}>" + self.inline(b.children) + f"</h{level}>")
            elif isinstance(b, Paragraph):
                out.append("<p>" + self.inline(b.children) + "</p>")
            elif isinstance(b, ListBlock):
                items = "".join("<li>" + self.inline(item) + "</li>" for item in b.items)
                if b.ordered:
                    start = f' start="{b.start}"' if b.start != 1 else ""
                    out.append(f"<ol{start}>{items}</ol>")
                else:
                    out.append(f"<ul>{items}</ul>")
            elif isinstance(b, Table):
                def cell(tag: str, content: list, align: str) -> str:
                    style = f' style="text-align:{align}"' if align != "left" else ""
                    return f"<{tag}{style}>" + self.inline(content) + f"</{tag}>"

                head = "".join(cell("th", c, a) for c, a in zip(b.header, b.aligns))
                body = "".join(
                    "<tr>" + "".join(cell("td", c, a) for c, a in zip(row, b.aligns)) + "</tr>"
                    for row in b.rows
                )
                out.append(
                    f'<div class="table"><table><thead><tr>{head}</tr></thead>'
                    f"<tbody>{body}</tbody></table></div>"
                )
            elif isinstance(b, Quote):
                out.append("<blockquote>" + self.blocks(b.blocks) + "</blockquote>")
            elif isinstance(b, Rule):
                out.append("<hr>")
            else:  # pragma: no cover
                raise TypeError(b)
        return "\n".join(out)

    def footnotes_html(self) -> str:
        """Список сносок в порядке номеров; вызывать после всех blocks()."""
        if not self.order:
            return ""
        items = []
        for key in self.order:
            fid = self._id(key)
            backs = " ".join(
                f'<a href="#fnref-{fid}-{k}" class="back" aria-label="Назад к тексту">↩</a>'
                for k in range(1, self.refs[key] + 1)
            )
            items.append(f'<li id="fn-{fid}">' + self.inline(self.footnotes[key]) + f" {backs}</li>")
        return '<ol class="footnotes">' + "".join(items) + "</ol>"
