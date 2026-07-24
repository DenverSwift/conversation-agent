# Coding Agent Instructions

## Start With Context

- Read `README.md`, `plan.md`, and relevant files in `docs/` before making changes.
- Preserve existing notes and documentation unless the task explicitly asks to revise them.

## Change Discipline

- Make small, focused changes that match the requested task.
- Do not add business logic without a separate explicit assignment.
- Do not change architecture without explaining why the change is needed.
- Prefer the existing project layout and naming conventions.

## Safety

- Never commit secrets, tokens, API keys, Telegram session files, local databases, or `.env` files.
- Keep `.env.example` safe: variable names only, no real values.
- Avoid destructive Git commands and never force-push unless the user explicitly requires it.

## Verification

- Run relevant checks before committing.
- For scaffold-only changes, at minimum check repository status and validate configuration files.

## Git

- Commit only intentional project changes.
- Keep commits small and descriptive.
- Pull with rebase before pushing to avoid overwriting remote work.
