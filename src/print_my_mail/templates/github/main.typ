#set page(width: 48mm, height: auto, margin: (top: 1mm, bottom: 1mm, left: 0.2mm, right: 0mm))
#set text(font: "LXGW WenKai GB", size: 8pt)
#let wrap(x) = {
  x.codepoints().join(sym.zws)
}

#let t(repo: str, id: int, title: str, kind: str, label: str) = {
  let closed = kind == "closed-issue" or kind == "pr-merged" or kind == "pr-closed"
  let icon = if kind == "issue" {
    box(image("issue.svg", width: 8pt), baseline: 2pt)
  } else if kind == "closed-issue" {
    box(image("issue-closed.svg", width: 8pt), baseline: 2pt)
  } else if kind == "pr" {
    box(image("pr.svg", width: 8pt), baseline: 2pt)
  } else if kind == "pr-merged" {
    box(image("pr-merged.svg", width: 8pt), baseline: 2pt)
  } else if kind == "pr-closed" {
    box(image("pr-closed.svg", width: 8pt), baseline: 2pt)
  } else if kind == "release" {
    box(image("tag.svg", width: 8pt), baseline: 2pt)
  } else if kind == "commit" {
    box(image("commit.svg", width: 8pt), baseline: 2pt)
  } else if kind == "notification" {
    box(image("bell-dot.svg", width: 8pt), baseline: 2pt)
  } else {
    panic("unknown kind: " + kind)
  }

  let txt(x) = if closed {
    strike(text(x, size: 6pt))
  } else {
    text(x, size: 6pt)
  }

  let right_text = if label != "" { label } else { "#" + str(id) }
  align(right)[#txt(right_text)]
  v(-14pt)
  [
    #icon #txt(repo)
    #v(-7pt)
    #txt(title)
  ]
}

#let item(repo: str, id: int, title: str, kind: str, label: str, content: str) = {
  [
    #v(-7pt)
    #box(fill: rgb("#cccccc"), width: 100%, inset: 2pt)[
      #t(repo: repo, id: id, title: title, kind: kind, label: label)
    ]
    #v(-7pt)
    #text(
      weight: "light",
      size: 6pt,
    )[#content]
    #v(-5pt)
    #align(center)[#line(length: 80%, stroke: (thickness: 0.6pt))]
  ]
}

// ---

= #box(image("github.svg", width: 14pt), baseline: 3pt) GitHub 通知

#v(-4pt)
#align(center)[
  #set text(size: 6pt, weight: "bold")
  #table(
    columns: 2,
    inset: 3pt,
    stroke: (thickness: 0.6pt),
    [时间], [#text(wrap("{{time}}"))],
  )
]

{{items}}

// ---

#line(length: 100%, stroke: (dash: ("dot", 3pt, 3pt, 3pt), cap: "round", thickness: 0.8pt))
