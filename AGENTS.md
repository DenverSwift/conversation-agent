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

## Versioning

- Use the Cup Size progression `AAA`, `AA`, `A`, `B`, `C`, `D`, `DD`, `E`.
- A letter change introduces a new cognitive capability.
- A numeric subversion, such as `AAA.2`, represents engineering improvements within the same capability.
- Treat `docs/versioning.md` as the source of truth and do not reintroduce semantic milestone versions.
- Preserve historical prompt-version strings stored in local data; never rewrite them during a version update.
- The current base-personality release is `AA.2`; runtime style rules and retrieved
  human evidence must remain provider-independent and must never treat AI replies
  as Matvey-authored style evidence.
- Preserve incremental compiler compatibility: unchanged private evidence must
  reuse local cached observations, and analyzer changes require an explicit
  `--full-rebuild`.

## Git

- Commit only intentional project changes.
- Keep commits small and descriptive.
- Pull with rebase before pushing to avoid overwriting remote work.
