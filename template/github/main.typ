#set page(width: 48mm, height: auto, margin: (top: 5mm, bottom: 5mm, left: 0.2mm, right: 0mm))
#set text(font: "LXGW WenKai GB", size: 8pt)
#let wrap(x) = {
  x.codepoints().join(sym.zws)
}

#let t(repo: str, id: int, title: str, kind: str) = {
  let closed = kind == "closed-issue" or kind == "pr-merged"
  let icon = if kind == "issue" {
    box(image("issue.svg", width: 8pt), baseline: 2pt)
  } else if kind == "closed-issue" {
    box(image("issue-closed.svg", width: 8pt), baseline: 2pt)
  } else if kind == "pr" {
    box(image("pr.svg", width: 8pt), baseline: 2pt)
  } else if kind == "pr-merged" {
    box(image("pr-merged.svg", width: 8pt), baseline: 2pt)
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

  align(right)[#txt("#" + str(id))]
  v(-14pt)
  [
    #icon #txt(repo)
    #v(-7pt)
    #txt(title)
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

#v(-7pt)
#box(fill: rgb("#cccccc"), width: 100%, inset: 2pt)[
  {{items}}
]

#text(
  "{{content}}",
  weight: "light",
  size: 6pt,
)

// ---

#line(length: 100%, stroke: (dash: ("dot", 3pt, 3pt, 3pt), cap: "round", thickness: 0.8pt))
