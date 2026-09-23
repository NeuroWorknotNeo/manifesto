// Вёрстка PDF-версии манифеста.
//
// Текст лежит в text/*.md: tools/build.py переводит его в Typst и кладёт
// в build/ вместе с данными о подписях (data.json) и QR-кодом (qr.svg).
// Собирать командой `python3 tools/build.py`, а не typst напрямую.

#let dir = sys.inputs.at("build", default: "build")
#let data = json(dir + "/data.json")

#let ink = rgb("#1b365d")     // тёмно-синий: заголовки, ссылки, QR-код
#let muted = rgb("#5d6878")   // второстепенный текст
#let hairline = rgb("#c5cedb")
#let tint = rgb("#eef2f7")    // заливка плашек

#set document(
  title: data.title,
  author: "Студенты и аспиранты МГУ",
  keywords: ("МГУ", "искусственный интеллект", "образование", "наука"),
)
#set page(
  paper: "a4",
  margin: (top: 2.2cm, bottom: 2.3cm, x: 2.4cm),
  footer: context {
    set text(font: "PT Sans", size: 8.5pt, fill: muted)
    link(data.site_url, data.site_url_display)
    h(1fr)
    counter(page).display("1 из 1", both: true)
  },
)
#set text(font: "PT Serif", size: 11pt, lang: "ru", region: "ru", hyphenate: true)
#set par(justify: true, leading: 0.7em, spacing: 0.7em, first-line-indent: (amount: 1.3em, all: true))
#show link: set text(fill: ink)
// у PT Serif есть надстрочные только ¹²³, поэтому все номера сносок рисуем одинаково
#set super(typographic: false)

#show heading: set text(font: "PT Sans", fill: ink, hyphenate: false)
#show heading.where(level: 1): it => block(above: 1.55em, below: 0.75em, sticky: true, text(size: 13pt, it.body))
#show heading.where(level: 2): it => block(above: 1.25em, below: 0.6em, sticky: true, text(size: 11.5pt, it.body))

#set list(indent: 1.3em, body-indent: 0.55em, spacing: 0.55em, marker: text(fill: ink)[—])
#set enum(indent: 1.3em, body-indent: 0.55em, spacing: 0.55em, numbering: n => text(fill: ink, weight: "bold")[#n.])

#set footnote.entry(separator: line(length: 22%, stroke: 0.5pt + hairline), gap: 0.45em, clearance: 0.9em)
#show footnote.entry: it => {
  // номер слева, текст сноски с висячим отступом
  let loc = it.note.location()
  let num = numbering(it.note.numbering, ..counter(footnote).at(loc))
  set text(size: 8.3pt, fill: muted)
  set par(justify: false, first-line-indent: 0pt, leading: 0.5em)
  grid(columns: (1.5em, 1fr), link(loc, num + "."), it.note.body)
}

#set table(
  stroke: none,
  inset: (x: 7pt, y: 5pt),
  fill: (_, y) => if y == 0 { ink } else if calc.even(y) { tint },
)
#show table: set text(size: 9.8pt, hyphenate: false)
#show table: set par(justify: false, first-line-indent: 0pt, leading: 0.55em)
#show table.cell.where(y: 0): set text(font: "PT Sans", fill: white, weight: "bold")

// ----------------------------------------------------------------- шапка

#block(below: 1.4em, {
  set par(justify: false, first-line-indent: 0pt)
  block(below: 11pt, text(font: "PT Sans", size: 9pt, weight: "bold", tracking: 0.2em, fill: ink)[МАНИФЕСТ])
  block(below: 13pt, text(size: 24pt, weight: "bold", fill: ink, hyphenate: false, data.title))
  block(below: 15pt, text(size: 12pt, style: "italic", fill: muted, hyphenate: false, data.subtitle))
  line(length: 100%, stroke: 0.9pt + ink)
  block(above: 7pt, text(font: "PT Sans", size: 8.5pt, fill: muted)[
    Редакция #data.edition от #data.edition_date
    #h(1fr)
    Подписей: #data.total · обновлено #data.updated
  ])
})

// вводный абзац — крупнее остального текста
#block(below: 1.1em, {
  set text(size: 12pt)
  set par(first-line-indent: 0pt, leading: 0.68em)
  include dir + "/lead.typ"
})

#include dir + "/body.typ"

// ------------------------------------------------------ призыв подписать

#let sign-box(title, note) = block(
  breakable: false,
  above: 1.6em,
  below: 1.4em,
  width: 100%,
  fill: tint,
  radius: 3pt,
  inset: (x: 14pt, y: 13pt),
  {
    set par(justify: false, first-line-indent: 0pt)
    grid(
      columns: (1fr, auto),
      column-gutter: 18pt,
      align: (left + horizon, right + horizon),
      [
        #text(font: "PT Sans", size: 12.5pt, weight: "bold", fill: ink, title)
        #v(0.2em)
        #note
      ],
      image(dir + "/qr.svg", width: 2.6cm),
    )
  },
)

#sign-box(
  [Поддержите предложение],
  [Прочитать манифест и подписать его можно на сайте
    #link(data.site_url, data.site_url_display) или по QR-коду.
    Список подписавших обновляется автоматически: свежая версия
    этого документа всегда лежит на сайте.
    #if data.organizer != "" and data.contact != "" [
      Организатор сбора подписей: #data.organizer, #data.contact.
    ]],
)

// -------------------------------------------------------------- приложение

#pagebreak(weak: true)
#include dir + "/appendix.typ"

// ------------------------------------------------------------------ подписи

// Подписи идут по строкам: 1 и 2 в первой строке, 3 и 4 во второй и т. д.
// Так обе колонки всегда одинаковой длины, а список спокойно переходит
// со страницы на страницу.
#let signature-list(entries) = {
  set text(font: "PT Sans", size: 9.4pt)
  grid(
    columns: (1fr, 1fr),
    column-gutter: 1.5em,
    row-gutter: 0.48em,
    // явный par: в ячейке сетки текст сам по себе абзацем не считается,
    // и без него не работает висячий отступ у перенесённых строк
    ..entries.map(e => par(justify: false, first-line-indent: 0pt, hanging-indent: 2.3em, leading: 0.4em)[
      #box(width: 2.3em)[#h(1fr)#text(fill: muted)[#e.n.]#h(0.5em)]#e.name#text(fill: muted)[ — #e.detail]
    ]),
  )
}

#pagebreak(weak: true)
#heading(level: 1)[Подписи]

#{
  set par(first-line-indent: 0pt)
  if data.total > 0 [*#data.total_label* на #data.updated #data.summary] else [#data.summary]
}

#if data.total == 0 {
  sign-box(
    [Подпишите первым],
    [Манифест можно подписать на сайте #link(data.site_url, data.site_url_display)
      или по QR-коду — подпись появится в этом списке автоматически.],
  )
} else {
  if data.by_faculty.len() > 1 {
    block(above: 1em, below: 1.3em, {
      set text(font: "PT Sans", size: 9pt, fill: muted)
      set par(justify: false, first-line-indent: 0pt)
      grid(
        columns: (1fr, 1fr, 1fr),
        column-gutter: 1.6em,
        row-gutter: 0.45em,
        ..data.by_faculty.map(row => [#row.faculty #box(width: 1fr, repeat(gap: 0.15em)[.]) #row.count]),
      )
    })
  }
  if data.students.len() > 0 {
    heading(level: 2)[Студенты и аспиранты]
    signature-list(data.students)
  }
  if data.supporters.len() > 0 {
    heading(level: 2)[Поддержали: преподаватели, сотрудники и выпускники]
    signature-list(data.supporters)
  }
}
