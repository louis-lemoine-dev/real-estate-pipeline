# Notion and project docs

## Notion
- Notion is the roadmap and documentation layer; GitHub is the source of truth for implementation. Notion is reached through the Notion MCP connection (`/mcp`).
- The roadmap is a database. List the steps of a phase with `notion-query-data-sources` on `collection://03650849-ad7e-82b6-b391-07ebe1cf26cb` (`SELECT "Step", "Status", … WHERE "Phase" = '…'`). Never rely on `notion-search` or a page's children to know what exists: a whole step (3.4) was once missed that way.
- Every completed task gets a write-up in the order **Context → What was done → What was learned**. Later corrections are appended after a `---` as "Amendment (date, PR #n)". Draft the content, and write it to Notion once Louis agrees.
- Louis sets Status, Scope and Done himself: never touch them. When it is unclear which item to touch, ask ("if I say 1.2, work on 1.2").
- Page content is stored with inline markup (`<b>`, `<code>`): fetch the page first so that an `old_str` matches the stored form. `insert_content` with `position: {type: end}` appends; `replace_content` replaces the whole body, so use it with care.
- Notion `alter column` drops `COMMENT` annotations: re-add field descriptions in the UI afterwards.

## Repo docs (`docs/`)
- `progress.md` is the cumulative log, `decisions.md` holds decisions with their reasons, `parking-lot.md` open and resolved items, `tools-and-gotchas.md` reference material, `project-context.md` stable background, `git-guide.md` the git reference (the Notion copy is separate), `archive/` old material that is not a rule.
- Update the right file at the time. A decision goes into `decisions.md` with its reason, not only into the chat.
- Status recaps to Louis are in plain English.
- Auto memory (`/memory`) is a supplement, not the record. Anything durable goes into `docs/` or these rules.
