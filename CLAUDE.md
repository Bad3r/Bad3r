# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The `Bad3r` GitHub profile README. `README.md` shows three SVG cards from
`profile/`, each in dark and light, chosen by `<picture>` sources. The cards are
generated, never hand-edited: `scripts/generate_cards.py` writes them and
`.github/workflows/profile-cards.yml` commits them as "Update README cards"
(daily, on manual dispatch, and on pushes that touch the script, workflow or
`theme`). To hide those bot commits:
`git log --invert-grep --grep='Update README cards'`.

`theme/` is a git submodule of https://github.com/Bad3r/github-primer-theme
(Primer tokens and Octicons). Run `git submodule update --init` after cloning.
To change colours or icons, change that repo, then commit the new submodule
commit here.

## Commands

The generator is stdlib-only, because CI runs it with the system `python3`.

```sh
# Render into a scratch dir so the committed SVGs stay untouched.
GITHUB_TOKEN="$(gh auth token)" python3 scripts/generate_cards.py \
  --login Bad3r --out-dir "$TMPDIR/cards" --langs-count 6 \
  --hide-langs 'HTML,CSS,XSLT,Perl,Vim Script,Scheme,Roff,CMake,Vue,Astro' \
  --exclude-repos 'odd-etc-backup'
```

The workflow's `Generate cards` step holds the real flags. CI uses the
`PROFILE_TOKEN` secret (a classic PAT with `read:user` and `repo`). Without a
user token the private count is 0, and the totals and rank change.

There are no tests or lint config. Check changes with `ruff check`, by parsing
the SVGs, and with browser screenshots in both themes. `write_cards()` is
separate from fetching, so you can re-render from saved API data. The global
`ruff` config uses line length 88, but the script is not formatted to it. Don't
reformat it wholesale.

## How `generate_cards.py` works

- **Fetch:** `graphql()` retries 403, 429 and 5xx. `fetch_contributions` asks
  for one year at a time.
- **Fold:** today never breaks the current streak. Private contributions add to
  the total only. `calculate_rank` ports github-readme-stats.
- **Render:** `svg_document()` draws the shared box: 846px wide, the README
  column on desktop, styled like the Pinned cards.
  - Stats: the total, with private work named in the same sentence, plus a rank
    pill and a row of icon counts.
  - Languages: ranked bars in each language's colour.
  - Activity: weekly green columns scaled to the busiest week.

  `text_width()` estimates text widths, so check spacing in a browser after
  changing text or sizes.
- **Theme:** `ROLES` maps card roles to Primer token names in
  `theme/tokens.json`. `ICONS` lists the Octicons used from `theme/icons/`.

## Easy to break

- README images load as `<img>`, so no webfonts or external resources. Use the
  Primer font stack.
- GitHub's `themed-picture` rewrites the whole `media` attribute of any source
  that mentions `prefers-color-scheme`. Never add other conditions to those
  queries, such as `max-width`.
- Keep each card's `<title>`/`<desc>` and the README `alt` text accurate.
- Escape interpolated text with `html.escape`.
- Commit messages follow Conventional Commits, with the reasoning and the
  validation run in the body.
