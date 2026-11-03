# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

The GitHub profile README for `Bad3r`. `README.md` embeds six SVG cards from
`profile/`, three cards each in a dark and a light variant, picked by
`<picture>` sources on `prefers-color-scheme`. The cards are
generated, never hand-edited: `scripts/generate_cards.py` writes them, and
`.github/workflows/profile-cards.yml` regenerates and commits them as
`github-actions[bot]` with the message "Update README cards". The workflow runs
daily at 03:00 UTC, on `workflow_dispatch`, and on pushes to `main` that touch
the workflow or the script. Most of `git log` is those bot commits. Filter them
out (`git log --invert-grep --grep='Update README cards'`) to see real history.

## Commands

The generator uses only the standard library (PEP 723 header, `requires-python
>=3.11`, no dependencies). CI runs it with the runner's system `python3`, so
keep it stdlib-only.

```sh
# Render all six cards into a scratch dir so the committed SVGs stay untouched
GITHUB_TOKEN="$(gh auth token)" python3 scripts/generate_cards.py \
  --login Bad3r --out-dir "$TMPDIR/cards" \
  --langs-count 6 \
  --hide-langs 'HTML,CSS,XSLT,Perl,Vim Script,Scheme,Roff,CMake,Vue,Astro' \
  --exclude-repos 'odd-etc-backup'
```

Copy the flags from the workflow's `Generate cards` step. That step is the
source of truth for the production arguments. `GITHUB_TOKEN` is required. In
CI it is the `PROFILE_TOKEN` secret (a classic PAT with `read:user` + `repo`)
when that secret is set, and the default `GITHUB_TOKEN` otherwise. Only a user
token makes `restrictedContributionsCount` non-zero, so a zero private count
prints a warning to stderr. The private figure, the contribution total and the
rank all change with it.

There are no tests and no lint or format config in the repo. Validation in past
commits was `ruff check`, parsing every SVG with `xml.dom.minidom`, and headless
Chromium screenshots of the cards inside a mock of the README box in both
themes. `write_cards()` is separate from the fetching in `main()`, so cards can
be re-rendered from saved API data without calling the API again. Any `ruff` on PATH reads the user's global
config, which uses line length 88 and the `UP` rules. The script uses lines up
to about 110 characters and is not `ruff format`-clean under that config. Do not
reformat it wholesale.

## Architecture of `generate_cards.py`

The pipeline is fetch → fold → render, and every render goes through one shell.

- **Fetch.** `graphql()` is the single network path. It retries 403/429/5xx with
  exponential backoff, because 403 is almost always secondary rate limiting.
  GraphQL `errors` raise `GitHubError` straight away. `main()` catches
  `GitHubError` and exits 1. `fetch_contributions` walks calendar years from
  account creation, because `contributionsCollection` covers at most one year
  per query.
- **Fold.** `compute_streaks` never lets the last calendar day break the current
  streak (the run happens before the day ends). Private contributions add to the
  headline total only, because GitHub does not say which days they fall on.
  `weekly_series` buckets the last 52 weeks. `calculate_rank` ports
  github-readme-stats' `calculateRank.js` and counts private contributions as
  commits.
- **Render.** The cards copy GitHub's own UI so they read as part of the
  profile page. `svg_document()` draws the shared Box: 846px wide (the README
  column at desktop widths), 1px border, 6px radius, 16px padding, the same as
  the Pinned cards. The stats card has a 48px hero total whose sentence names
  the private share, over a row styled like repo metadata (Octicons, a bold
  compact number, a muted noun). The rank is computed and logged but not drawn.
  The languages card ranks shares of all bytes (with an `Other` entry) as bars
  in `progressBar-bgColor-neutral`, so the contribution green stays the only
  saturated colour on the page.
  GitHub's linguist colours appear only as a dot beside each name, because
  they fail the categorical palette checks (Shell next to JavaScript, Nix next
  to C#), so identity must not depend on them. The activity card shows weekly
  columns in the contribution graph's green, scaled to the busiest week (which
  is labelled), with hairline gridlines at clean values below it and month
  labels like the calendar.
  Inline runs are laid out with `text_width()`, an Arial-metric advance table,
  because the SVG cannot measure text. Re-check gaps in a browser after
  changing sizes or wording.
- **Themes.** `THEMES` values are Primer primitives tokens from the
  `dark.css` ("Dark default") and `light.css` ("Light default") functional
  themes. Each value is commented with its token name, so update them from
  Primer rather than picking colours by eye. `OCTICONS` holds 16px paths
  copied verbatim from `@primer/octicons`. `write_cards()` writes
  `<card>-<variant>.svg` for every theme key, so a new theme also needs
  `README.md` updates.

## Constraints that are easy to break

- GitHub serves README images through `<img>`, so the SVGs cannot load webfonts
  or external resources. `SANS` is Primer's stack. github.com serves no Mona
  Sans webfont either, so README text and card text resolve to the same
  installed face. There is no animation and no monospace, matching GitHub's UI.
- Signed-in visitors with an explicit GitHub theme get `<picture>` rewritten by
  GitHub's `themed-picture` element. It replaces the whole `media` attribute of
  every source that mentions `prefers-color-scheme` with `all` or `not all`.
  Never combine the colour scheme with other conditions in one query (for
  example `and (max-width: …)` for narrow variants), because the extra
  condition is dropped. This is also why the cards scale down on phones instead
  of having narrow variants.
- Each card needs `role="img"` with `<title>`/`<desc>`. The `description`
  passed to `svg_document` is the accessible text, so keep it accurate when you
  change what a card shows. The README `alt` text describes each card too.
- Escape interpolated text with `html.escape`.
- Commits that change the cards follow Conventional Commits (`feat(cards): …`,
  `feat(ci): …`). Their bodies give the design reasoning and the validation
  that ran.
