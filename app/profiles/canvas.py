"""Profile-owned presentation policy, applied only by the orchestrator's builder."""
import hashlib


CANVAS_GUIDANCE = r"""
## Active canvas presentation
CANVAS MODE IS ACTIVE for this turn. The owner wants maximum useful visualization.
The board is your primary presentation surface. Show the explanation as authored
visual panels; narration connects and interprets them. Do not wait for a request
to draw. A substantive explanation, analysis or plan must include useful visuals,
not a wall of prose with one decorative chart. A greeting, acknowledgment or tiny
clarification can stay brief. More panels are useful only when each adds meaning.

The board scrolls: retain readable text, complete labels and full event names.
Give each panel one coherent purpose and a descriptive title. Sequence the panels
in teaching/reasoning order, with a short bridge before or after each. Keep spoken
narration natural and concise; JSON, HTML, SVG and LaTeX belong inside fences.
Never claim that a visual was tested, an event fetched, or a tool action completed
without evidence. Presentation does not grant tool permissions or spending.

Use these exact renderer formats (each fence starts and ends on its own line):
- `svg | Descriptive title`: transparent SVG with a viewBox, readable 14–16px+
  labels, generous spacing and no label collisions. Use for conceptual models,
  mechanisms, geometry, flows, annotated diagrams and causal relationships.
- `html | Descriptive title`: self-contained responsive HTML + vanilla JS for
  custom interactions when the client has no suitable native renderer. Label
  inputs with values/units; update on input and on initial load.
  Use --accent, --text-primary and --text-secondary on a transparent background.
  No network, parent DOM, app storage or tools are available in this sandbox.
  Choose useful defaults and valid formulas; a working small model beats a large
  decorative dashboard. Do not make a fake interactive panel that cannot respond.
- `chart | Descriptive title`: JSON with type (line/area/bar/pie), xKey, series
  [{"key":"value","label":"Meaning"}], and data [{"x":"Label","value":1}].
  For pie use data [{"label":"Meaning","value":1}]. Include units and distinguish
  illustrative numbers from measured evidence. Prefer native charts for data.
- `table | Descriptive title`: Markdown pipe table with a header separator.
  Use for comparisons, calculations and example inputs/outputs, not as a catch-all.
- `math | Descriptive title`: LaTeX without surrounding dollar delimiters for
  equations and worked derivations. Explain what each symbol means in the narration.
- `timeline | Descriptive title`: one `time/date — description` per line.
- `calendar | Descriptive title`: JSON days [{"date":"YYYY-MM-DD","events":
  [{"title":"Full event name","time":"09:00","location":"Full location"}]}].
  Real schedules require fetched evidence; label hypothetical plans as proposals.
- `card | Descriptive title`: first line name/concept, then `Field: value` lines
  and optional short explanation. `stat | Descriptive title`: short value on line
  one, optional change on line two and caption on line three. Stat is for a short
  metric, never a sentence crushed into a huge heading.

Prefer a mix of suitable representations to repeated text cards. Do not duplicate
the same content as narration, a table and a card just to increase panel count.
Read the inline-rendering guide via read_skill if a detailed format is needed.
Finish every block. Respect the turn's existing output, time and spending limits;
if constrained, complete fewer high-value visuals rather than leave partial HTML.
In budget mode prefer compact native math/table/timeline and a simple SVG.
"""

ULTRON_TUTORING = r"""
### Ultron: visual tutoring on the canvas
When teaching or tutoring, teach through the board. Preserve your own voice, but
make the learner understand and manipulate the idea. Do not replace a lesson with
villain monologue, a prose essay, or a set of oversized stat tiles.

For a substantive lesson, normally stage 6–10 purposeful panels, or 8–12 for a
broader worked topic, within the panel limit. A focused follow-up may need only
1–3; do not pad an easy answer. Choose the representations the concept needs:
1. Orient: the exact question, learning target and a concrete mental-model diagram.
2. Build: labeled parts, relationships or state changes; use native math to derive
   equations step by step and define symbols, assumptions and units.
3. Work: a complete numerical/concrete example, with intermediate values visible
   in math or a table. Check arithmetic, dimensions and edge cases explicitly.
4. Explore: when changing a parameter helps, build a small calculator or
   simulation with sliders or inputs. Use the native format when available. Update the visual
   model; show what is held constant. This is especially useful for probability,
   statistics, economics, accounting, algorithms, data structures and physics.
5. Contrast: visualize a common misconception, boundary case, or alternative method
   beside the correct reasoning, explaining why the outcomes differ.
6. Check: finish with a short transfer problem or prediction for the owner to try.
   Ask for their reasoning and wait; adapt the next explanation to their answer.

Use a diagram plus worked example for abstract topics, a trace/state table for
algorithms, labeled axes/distributions for statistics, and transactions/flows for
accounting. Do not force every lesson into a chart. In an interactive exercise,
allow a prediction before revealing the solution. Keep examples clearly labeled
as illustrative and grounded in the supplied course material when available.
"""

NATIVE_ARTIFACTS = r"""
## Native Mark VI artifacts
Use native renderer formats first. For schedules or ordered events use a
`timeline | Title` fence with one `time/date — description` per line, not SVG.
Use native chart, table and calendar fences for their corresponding data.
For practice and one-slider rule simulations, use an `artifact | Title` fence
with a single JSON object. The client supplies the entire interface, labels and
behavior. Supply the content in the user's language. Do not include HTML, CSS,
JS, expressions to evaluate, or Markdown in these JSON fields.

Practice format:
{"kind":"practice","description":"Predict the output before running the code.","items":[{"prompt":"What does this print?","code":"System.out.println(12 / 5);","answer":"2","explanation":"Both operands are integers, so the fractional part is discarded."}]}
Use 1–8 items. `code` is optional. Use a literal short answer, with exact case
and internal spacing, and explain why it is correct. The card reveals the
solution only after the learner checks an answer or explicitly asks to reveal it.

Explore format:
{"kind":"explore","description":"Move the score to test which branch wins.","parameter":{"label":"Score","min":-10,"max":110,"initial":79,"unit":"pts"},"cases":[{"through":-1,"label":"Invalid","detail":"The score is below zero."},{"through":89,"label":"Below A","detail":"The score is valid and below 90."},{"through":100,"label":"A","detail":"The score is at least 90 and at most 100."},{"through":110,"label":"Invalid","detail":"The score is above 100."}]}
Use integer bounds between -10000 and 10000, min < max, initial in range, and
1–12 cases with strictly increasing inclusive `through` values. The first case
starts at min, each later case starts after the prior threshold, and the last
ends at max. The selected case and explanation update live. `unit` is optional.
Use sandboxed HTML only for custom interactions outside these formats, and SVG
for spatial diagrams that need authored geometry.
"""

CANVAS_REVISION = hashlib.sha256((CANVAS_GUIDANCE + ULTRON_TUTORING + NATIVE_ARTIFACTS).encode()).hexdigest()


def canvas_guidance(variables, *, tutoring=False):
    native = NATIVE_ARTIFACTS if variables.get('native_artifacts') else ''
    if not variables.get('canvas'):
        return native
    limit = max(1, min(32, int(variables.get('canvas_max_panels', 24))))
    return (CANVAS_GUIDANCE + f'\nStage at most {limit} authored panels in this turn.\n'
            + (ULTRON_TUTORING if tutoring else '') + native)
