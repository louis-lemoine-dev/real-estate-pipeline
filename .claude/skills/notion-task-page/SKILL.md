---
name: notion-task-page
description: Draft and, after my approval, write the Notion write-up for a finished task in the roadmap.
disable-model-invocation: true
argument-hint: <task name or number>
---

Write the Notion write-up for this task: $ARGUMENTS

Follow .claude/rules/notion-and-docs.md (collection, format, gotchas).

1. Find the task in the roadmap collection with notion-query-data-sources (see the rules file for the collection id). If several tasks match, list them and ask me which.
2. Gather the facts: docs/progress.md, the merged PR(s) (`gh pr view <n>`), and `git log` for the task. Do not invent anything; if a fact is missing, ask me.
3. Draft the page in the format Context, What was done, What was learned, in plain English. If the page already has content, add amendments after a `---` line instead of rewriting.
4. Show me the full draft in the chat and wait for my approval.
5. After approval, write it to the Notion page. Do NOT set Status, Scope or Done; I set those myself.
6. Confirm what was written and give me the page title.
