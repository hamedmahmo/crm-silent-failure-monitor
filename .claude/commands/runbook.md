---
description: Regenerate docs/runbook.md from the Makefile, CLI help and config
argument-hint: [anything specific to add]
---

Bring `docs/runbook.md` up to date with the repository as it is now. Extra notes: $ARGUMENTS

1. Read `Makefile`, `pyproject.toml`, `.python-version`, `.pre-commit-config.yaml` and `.github/workflows/ci.yml`.
2. Run `make help` and `uv run python <script> --help` for every CLI script (at least `data/generate.py`).
3. Update these sections and keep their order: Requirements, First-time setup, Everyday commands, Generating data, CI, Troubleshooting. Add a section for each project-specific command.
4. Every command in the runbook must be one you just ran or read in a config file. Run the short ones (`make help`, `make test`, `--help`) to confirm they work on this machine.
5. Keep the Windows notes. Commands must work in PowerShell and in bash.
6. Add any problem you hit during this session to the Troubleshooting table, with the fix.
7. Don't print, read or document the values in `.env`. Variable names from `.env.example` are fine.

Plain language, short sentences, no filler. Show me the diff when you are done.
