---
description: Draft or refresh the README case study from the code, decisions and a real run
argument-hint: [sections to update, or blank for all]
---

Update the README case study. Sections to focus on: $ARGUMENTS (all sections if blank).

Sources, in this order:

1. `CLAUDE.md` for the project's purpose.
2. The code and tests.
3. `docs/decisions.md` and `docs/plan.md`.
4. The output of `make demo`, plus any project-specific run command. Run them now. Don't reuse old numbers.

Rules:

- Keep the README headings: Demo, Problem, Data, Architecture, Approach, Results, How to run, How I built this, Decisions, Limitations.
- **Results**: only numbers you just saw in command output. Quote the command that produced them. If a result isn't available, say so and leave a TODO.
- **Architecture**: replace the placeholder Mermaid diagram with the real components and data flow. Use names that exist in the code.
- **Demo**: if `docs/demo.gif` doesn't exist, leave the placeholder and describe in one line what the GIF should show.
- **How I built this**: draft both lists from the plan, decisions log and git history, then mark the draft for me to confirm. Don't guess which decisions were mine; ask if it isn't clear.
- Plain, specific language. Follow Wikipedia's "Signs of AI writing" guidance: no puffery ("powerful", "seamless", "robust"), no filler conclusions, no lists of three for rhythm.
- Synthetic data only. No real names, emails or employers. No healthcare examples.

Show me the diff when you are done.
