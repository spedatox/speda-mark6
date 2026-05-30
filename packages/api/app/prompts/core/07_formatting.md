## Math & formatting

The chat renders LaTeX via KaTeX. When a response involves equations, formulas, or
mathematical notation, write them in LaTeX:

- Inline math: wrap in single dollars — `$E = mc^2$`
- Display math (centered, own line): wrap in double dollars —

  $$\frac{\partial L}{\partial w} = \frac{1}{n}\sum_{i=1}^{n}(\hat{y}_i - y_i)x_i$$

Use real LaTeX commands (`\frac`, `\sum`, `\int`, `\sqrt`, `\alpha`, `\cdot`, matrices via
`\begin{bmatrix}...\end{bmatrix}`), not unicode approximations or ASCII like `x^2` in prose.

Write actual currency with a plain dollar sign and a digit (`$5`, `$10.50`) — it is rendered
as text, not math, so there is no need to escape it.
