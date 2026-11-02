#set page(width: 48mm, height: auto, margin: (top: 5mm, bottom: 5mm, left: 0.2mm, right: 0mm))
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
    [收], [#text(wrap("sbchild0@gmail.com"))],
    [发], [#text(wrap("aaaa@sssss.com"))],
    [时间], [#text(wrap("2026/10/10 10:24:59"))],
  )
]
#v(-8pt)
#box(fill: rgb("#cccccc"), width: 100%, inset: 2pt)[
  #text("This is it — doors close in a few hours
", weight: "bold")

  #align(right)[
    -- #text("Will from EDM Tips", size: 7pt)
  ]
]
#v(-8pt)

#text(
  "Hey Sbchild,

Just a final reminder that enrolment for the Music Production Accelerator closes at midnight tonight (PST).

And our new intake starts tomorrow!

If you've been thinking about joining us, I'd love to help you take that next step.

Imagine looking back in a few months, having finished music you're genuinely proud of, knowing exactly how to improve your productions, and feeling excited about what you're capable of creating next.",
  weight: "light",
  size: 6pt,
)

// ---

#line(length: 100%, stroke: (dash: ("dot", 3pt, 3pt, 3pt), cap: "round", thickness: 0.8pt))
