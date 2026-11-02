#set page(width: 48mm, height: auto, margin: (top: 1mm, bottom: 1mm, left: 0.2mm, right: 0mm))
#set text(font: "LXGW WenKai GB", size: 8pt)
#let wrap(x) = {
  x.codepoints().join(sym.zws)
}


// ---

= #box(image("mail-dot.svg", width: 14pt), baseline: 3pt) 新邮件

#align(center)[
  #set text(size: 6pt, weight: "bold")
  #table(
    columns: 2,
    inset: 3pt,
    stroke: (thickness: 0.6pt),
    [收], [#text(wrap("{{recp}}"))],
    [发], [#text(wrap("{{sender}}"))],
    [时间], [#text(wrap("{{time}}"))],
  )
]
#v(-8pt)
#box(fill: rgb("#cccccc"), width: 100%, inset: 2pt)[
  #text("{{title}}", weight: "bold")
  #v(-5pt)
  #align(right)[
    -- #text("{{sender_name}}", size: 7pt)
  ]
]
#v(-8pt)

#text(
  "{{content}}",
  weight: "bold",
  size: 6pt,
)

// ---

#line(length: 100%, stroke: (dash: ("dot", 3pt, 3pt, 3pt), cap: "round", thickness: 0.8pt))
