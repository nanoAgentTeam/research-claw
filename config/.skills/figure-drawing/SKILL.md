---
name: figure-drawing
description: |
  Academic figure creation skill. Activate when a task involves creating
  figures, diagrams, charts, or visualizations for a paper.
allowed-tools:
  - read_file
  - write_file
  - str_replace
  - bash
  - latex_compile
---

# Academic Figure Drawing

Create publication-quality figures for academic papers. The final output MUST be
a **PNG image** included via `\includegraphics` — never `\input` a raw .tex file
into main.tex.

## SOP

### Step 1: Plan the figure

Before writing any code, decide:
- **Type**: taxonomy tree, architecture diagram, flowchart, bar/line chart, comparison table, timeline
- **Content**: what nodes/elements to show (read the relevant paper sections first)
- **Layout direction**: top-to-bottom or left-to-right (pick whichever avoids crowding)
- **Estimated element count**: if > 20 nodes, split into multiple figures

### Step 2: Write the TikZ source

Write a **standalone** .tex file in `figures/`:

```latex
\documentclass[tikz,border=10pt]{standalone}
\usepackage[T1]{fontenc}
\usepackage{tikz}
\usetikzlibrary{positioning, arrows.meta, shapes, fit, backgrounds}
% Add other libraries as needed

\begin{document}
\begin{tikzpicture}[...]
  % figure content
\end{tikzpicture}
\end{document}
```

### Step 3: Compile to PDF, then convert to PNG

```bash
cd figures/
pdflatex -interaction=nonstopmode xxx.tex
# Convert PDF to PNG (try available tools in order)
pdftoppm -png -r 300 xxx.pdf xxx_fig  # produces xxx_fig-1.png
# OR: convert xxx.pdf -density 300 xxx.png
# OR: sips -s format png xxx.pdf --out xxx.png  (macOS)
```

If `pdftoppm` / `convert` / `sips` are all unavailable, keep the PDF and use
`\includegraphics{figures/xxx.pdf}` as fallback.

### Step 4: Insert into paper

```latex
\begin{figure}[t]
  \centering
  \includegraphics[width=\columnwidth]{figures/xxx.png}
  \caption{Descriptive caption.}
  \label{fig:xxx}
\end{figure}
```

### Step 5: Visual check

After compiling the full paper, verify the figure renders correctly and is
referenced in the text with `\ref{fig:xxx}`.

## Layout & Anti-overlap Rules (CRITICAL)

These rules prevent the most common failure — elements overlapping each other:

1. **Use relative positioning, never absolute coordinates.**
   ```latex
   \node[right=2cm of A] (B) {...};   % GOOD
   \node at (3,2) (B) {...};          % BAD — will overlap when content changes
   ```

2. **Minimum spacing between nodes:**
   - Siblings (same level): ≥ 1.2cm vertical, ≥ 2.0cm horizontal
   - Parent-child: ≥ 1.8cm
   - If text is long, increase spacing proportionally

3. **Set minimum node size to fit content:**
   ```latex
   minimum width=3cm, minimum height=0.9cm, text width=2.6cm, align=center
   ```
   Use `text width` to force line-wrapping for long labels.

4. **For tree/taxonomy diagrams with many leaves:**
   - Use `grow=right` or `grow=down` with `level distance=2.5cm, sibling distance=1.5cm`
   - If > 5 siblings at one level, increase `sibling distance` or stack in two columns

5. **Test for overlap:** after compilation, check the PDF visually. If any
   elements overlap, increase spacing and recompile before finalizing.

## Style Guidelines

**Colors** — use muted, professional tones:
- Branch A: `fill=blue!10, draw=blue!40`
- Branch B: `fill=teal!10, draw=teal!40`
- Branch C: `fill=orange!10, draw=orange!40`
- Branch D: `fill=violet!10, draw=violet!40`
- Backgrounds: `fill=gray!3`

**Text** — minimum `\footnotesize` for any label. Use `\small` for node text,
`\normalsize\bfseries` for titles. Never use `\tiny`.

**Lines** — `draw=gray!50`, arrow tip `-{Stealth[length=5pt]}`, line width 0.5pt.

**Overall** — generous whitespace. A figure that breathes is better than one
that packs everything in. When in doubt, make it wider/taller.
