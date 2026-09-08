# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

A bare `#42` may refer to an issue or pull request. Resolve it with
`gh pr view 42`, falling back to `gh issue view 42`.

## Skill operations

- "Publish to the issue tracker" means creating a GitHub issue.
- "Fetch the relevant ticket" means running `gh issue view <number> --comments`.

## Wayfinding operations

A wayfinding map is one issue labelled `wayfinder:map`, with child issues
representing tickets.

Use GitHub sub-issues and native issue dependencies when available. Otherwise,
link children from the map body, add `Part of #<map>` to each child, and
represent blockers with `Blocked by: #<number>`.
