#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render the profile README cards committed under profile/.

Stdlib only and no third-party services: the GitHub GraphQL API is the single
external dependency, so the workflow can run this with the runner's system
interpreter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
FALLBACK_LANGUAGE_COLOR = "#858585"
# GitHub's repository language bar paints the remainder in this colour in both themes.
OTHER_LANGUAGE_COLOR = "#ededed"

# The cards sit in the profile README box, whose content column is 846px wide
# at desktop widths, and follow the Pinned cards below it: 1px border, 6px
# radius, 16px padding.
CARD_WIDTH = 846
PAD = 16
CONTENT = CARD_WIDTH - 2 * PAD

# Primer's sans stack. GitHub serves no webfont for it, so README text and these
# cards resolve to the same installed face on every visitor's machine.
SANS = (
    "'Mona Sans VF', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
    "Helvetica, Arial, sans-serif"
)

# Primer primitives 11.10.0, functional themes dark.css ("Dark default") and
# light.css ("Light default").
THEMES = {
    "dark": {
        "canvas": "#0d1117",  # bgColor-default
        "border": "#3d444d",  # borderColor-default
        "fg": "#f0f6fc",  # fgColor-default
        "muted": "#9198a1",  # fgColor-muted
        "grid": "#3d444db3",  # borderColor-muted
        "ring": "#ffffff26",  # borderColor-translucent
        "bar": "#2ea043",  # contribution-default-bgColor-3
        "share_bar": "#656c76",  # progressBar-bgColor-neutral
    },
    "light": {
        "canvas": "#ffffff",
        "border": "#d1d9e0",
        "fg": "#1f2328",
        "muted": "#59636e",
        "grid": "#d1d9e0b3",
        "ring": "#1f232826",
        "bar": "#2da44e",
        "share_bar": "#59636e",
    },
}

# 16px Octicons 19.42.0 (MIT, GitHub Inc.).
OCTICONS = {
    "git-commit": (
        '<path d="M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 0-1.5h3.32a4.002 4.002 '
        '0 0 1 7.86 0h3.32a.75.75 0 0 1 0 1.5Zm-1.43-.75a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z"/>'
    ),
    "git-pull-request": (
        '<path d="M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 '
        "2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 "
        "13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 "
        "3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 "
        '0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z"/>'
    ),
    "issue-opened": (
        '<path d="M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z"/><path d="M8 0a8 8 0 1 1 0 16A8 '
        '8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z"/>'
    ),
    "star": (
        '<path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 '
        "1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 "
        "1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 "
        "1 8 .25Zm0 2.445L6.615 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 "
        ".216.664l-.528 3.084 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 "
        '.216-.664l2.24-2.183-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z"/>'
    ),
}


PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    createdAt
    followers { totalCount }
    pullRequests(first: 1) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
  }
}
"""

REPOSITORIES_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(
      first: 100
      after: $after
      ownerAffiliations: OWNER
      isFork: false
      orderBy: { field: STARGAZERS, direction: DESC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        stargazerCount
        languages(first: 10, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


class GitHubError(RuntimeError):
    pass


def graphql(token: str, query: str, variables: dict, *, attempts: int = 4) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "bad3r-profile-cards",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(GRAPHQL_ENDPOINT, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace").strip()[:400]
            # 403 here is almost always secondary rate limiting, not a scope problem.
            if error.code not in (403, 429, 500, 502, 503, 504):
                raise GitHubError(f"HTTP {error.code} from GraphQL API: {detail}") from error
            last_error = GitHubError(f"HTTP {error.code} from GraphQL API: {detail}")
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = GitHubError(f"GraphQL request failed: {error}")
        else:
            if body.get("errors"):
                messages = "; ".join(e.get("message", "?") for e in body["errors"])
                raise GitHubError(f"GraphQL API returned errors: {messages}")
            return body["data"]
        if attempt + 1 < attempts:
            time.sleep(3 * 2**attempt)
    raise last_error or GitHubError("GraphQL request failed")


@dataclass(frozen=True)
class Repository:
    name: str
    stars: int
    languages: tuple[tuple[str, str, int], ...]


@dataclass
class Streaks:
    total_contributions: int
    first_contribution: dt.date
    today: dt.date
    current_length: int
    current_start: dt.date
    current_end: dt.date
    longest_length: int
    longest_start: dt.date
    longest_end: dt.date


def fetch_profile(token: str, login: str) -> dict:
    user = graphql(token, PROFILE_QUERY, {"login": login})["user"]
    if user is None:
        raise GitHubError(f"No such GitHub user: {login}")
    return user


def fetch_repositories(token: str, login: str) -> list[Repository]:
    repositories: list[Repository] = []
    cursor = None
    while True:
        page = graphql(token, REPOSITORIES_QUERY, {"login": login, "after": cursor})
        page = page["user"]["repositories"]
        for node in page["nodes"]:
            languages = tuple(
                (edge["node"]["name"], edge["node"]["color"] or FALLBACK_LANGUAGE_COLOR, edge["size"])
                for edge in node["languages"]["edges"]
            )
            repositories.append(Repository(node["name"], node["stargazerCount"], languages))
        if not page["pageInfo"]["hasNextPage"]:
            return repositories
        cursor = page["pageInfo"]["endCursor"]


def fetch_contributions(token: str, login: str, created_at: dt.datetime, now: dt.datetime) -> dict:
    """Collect every contribution day plus commit, review and private totals.

    contributionsCollection caps a query at one year, so this walks calendar
    years from account creation to now.

    The calendar's per-day counts cover public repositories only. Work in
    private repositories is withheld from those days and reported solely as the
    restrictedContributionsCount aggregate, which needs a user token to be
    non-zero.
    """
    days: dict[str, int] = {}
    commits = 0
    reviews = 0
    restricted = 0
    for year in range(created_at.year, now.year + 1):
        window_start = max(created_at, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        window_end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        if window_start > window_end:
            continue
        collection = graphql(
            token,
            CONTRIBUTIONS_QUERY,
            {
                "login": login,
                "from": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )["user"]["contributionsCollection"]
        commits += collection["totalCommitContributions"]
        reviews += collection["totalPullRequestReviewContributions"]
        restricted += collection["restrictedContributionsCount"]
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]
    return {"days": days, "commits": commits, "reviews": reviews, "restricted": restricted}


def compute_streaks(days: dict[str, int], private: int = 0) -> Streaks:
    """Fold the contribution calendar into total, current and longest streaks.

    The final day never breaks the current streak: the card is generated before
    the day is over.

    `private` is added to the headline total only. GitHub does not say which
    days private contributions fall on, so they cannot extend a streak.
    """
    if not days:
        raise GitHubError("Contribution calendar came back empty")

    ordered = sorted(days.items())
    today = dt.date.fromisoformat(ordered[-1][0])
    streaks = Streaks(
        total_contributions=private,
        first_contribution=today,
        today=today,
        current_length=0,
        current_start=today,
        current_end=today,
        longest_length=0,
        longest_start=today,
        longest_end=today,
    )
    seen_contribution = False

    for iso_date, count in ordered:
        date = dt.date.fromisoformat(iso_date)
        streaks.total_contributions += count
        if count > 0:
            streaks.current_length += 1
            streaks.current_end = date
            if streaks.current_length == 1:
                streaks.current_start = date
            if not seen_contribution:
                streaks.first_contribution = date
                seen_contribution = True
            if streaks.current_length > streaks.longest_length:
                streaks.longest_length = streaks.current_length
                streaks.longest_start = streaks.current_start
                streaks.longest_end = streaks.current_end
        elif date != today:
            streaks.current_length = 0
            streaks.current_start = today
            streaks.current_end = today
    return streaks



def aggregate_languages(
    repositories: list[Repository], hidden: set[str], excluded: set[str], count: int
) -> list[tuple[str, str, float]]:
    """Top `count` languages as (name, colour, share), the rest folded into Other.

    Shares are of every byte left after the filters, as on a repository page,
    so they sum to one only with the Other entry.
    """
    totals: dict[str, list] = {}
    for repository in repositories:
        if repository.name.lower() in excluded:
            continue
        for name, color, size in repository.languages:
            if name.lower() in hidden:
                continue
            entry = totals.setdefault(name, [color, 0])
            entry[1] += size
    grand_total = sum(size for _, size in totals.values())
    if not grand_total:
        raise GitHubError("No languages left after applying the hide and exclude filters")
    ranked = sorted(totals.items(), key=lambda item: item[1][1], reverse=True)
    languages = [(name, color, size / grand_total) for name, (color, size) in ranked[:count]]
    rest = sum(size for _, (_, size) in ranked[count:])
    if rest:
        languages.append(("Other", OTHER_LANGUAGE_COLOR, rest / grand_total))
    return languages


def calculate_rank(
    *, commits: int, prs: int, issues: int, reviews: int, stars: int, followers: int
) -> tuple[str, float]:
    """Port of github-readme-stats calculateRank.js (all-time commit medians)."""

    def exponential_cdf(x: float) -> float:
        return 1 - 2**-x

    def log_normal_cdf(x: float) -> float:
        return x / (1 + x)

    weights = {"commits": 2, "prs": 3, "issues": 1, "reviews": 1, "stars": 4, "followers": 1}
    total_weight = sum(weights.values())
    score = (
        1
        - (
            weights["commits"] * exponential_cdf(commits / 1000)
            + weights["prs"] * exponential_cdf(prs / 50)
            + weights["issues"] * exponential_cdf(issues / 25)
            + weights["reviews"] * exponential_cdf(reviews / 2)
            + weights["stars"] * log_normal_cdf(stars / 50)
            + weights["followers"] * log_normal_cdf(followers / 10)
        )
        / total_weight
    )

    percentile = score * 100
    thresholds = [1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100]
    levels = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
    for threshold, level in zip(thresholds, levels):
        if percentile <= threshold:
            return level, percentile
    return levels[-1], percentile


def group_digits(value: int) -> str:
    return f"{value:,}"


def weekly_series(days: dict[str, int], weeks: int = 52) -> list[tuple[dt.date, int]]:
    """(first day, contribution total) for each of the most recent `weeks` weeks.

    Weeks end on the last calendar day, so the final bucket is the trailing
    seven days rather than a partial calendar week.
    """
    ordered = sorted(days.items())
    end = dt.date.fromisoformat(ordered[-1][0])
    start = end - dt.timedelta(days=weeks * 7 - 1)
    buckets = [0] * weeks
    for iso_date, count in ordered:
        index = (dt.date.fromisoformat(iso_date) - start).days // 7
        if 0 <= index < weeks:
            buckets[index] += count
    return [(start + dt.timedelta(days=7 * index), total) for index, total in enumerate(buckets)]


def plural(count: int, noun: str, nouns: str | None = None) -> str:
    return f"{group_digits(count)} {noun if count == 1 else nouns or noun + 's'}"


def svg_document(*, theme: dict, height: int, title: str, description: str, body: str) -> str:
    """The Box every card shares, drawn like the Pinned cards on the same page."""
    return f"""<svg
  xmlns="http://www.w3.org/2000/svg"
  width="{CARD_WIDTH}"
  height="{height}"
  viewBox="0 0 {CARD_WIDTH} {height}"
  role="img"
  aria-labelledby="title desc"
>
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(description)}</desc>
  <style>
    text {{ font-family: {SANS}; font-size: 14px; fill: {theme["fg"]}; }}
    .muted {{ fill: {theme["muted"]}; }}
    .strong {{ font-weight: 600; }}
    .small {{ font-size: 12px; }}
  </style>
  <rect
    x="0.5"
    y="0.5"
    width="{CARD_WIDTH - 1}"
    height="{height - 1}"
    rx="6"
    fill="{theme["canvas"]}"
    stroke="{theme["border"]}"
  />
{body}
</svg>
"""


# Advance widths in thousandths of an em, from Arial: Helvetica-metric faces are
# the most common resolution of the stack, and Segoe UI and SF land within a
# few percent of it.
_ADVANCE = {
    **dict.fromkeys("ijl", 222),
    "'": 191,
    **dict.fromkeys(" ,.:;ftI", 278),
    "r": 333,
    **dict.fromkeys("cksvxyzJ", 500),
    **dict.fromkeys("0123456789abdeghnopquL", 556),
    **dict.fromkeys("FTZ", 611),
    "+": 584,
    **dict.fromkeys("ABEKPSVXY", 667),
    **dict.fromkeys("CDHNRUw", 722),
    **dict.fromkeys("GOQ", 778),
    **dict.fromkeys("mM", 833),
    "W": 944,
}


def text_width(text: str, size: float, *, bold: bool = False) -> float:
    """Approximate advance width of `text`, for laying out inline runs.

    The visitor's browser renders the SVG with whatever face the stack resolves
    to, so this only has to be close enough to keep gaps even.
    """
    em = sum(_ADVANCE.get(char, 556) for char in text) / 1000
    return em * size * (1.04 if bold else 1.0)


def octicon(name: str, x: float, y: float, color: str) -> str:
    return f'  <g transform="translate({x:.1f} {y:.1f})" fill="{color}">{OCTICONS[name]}</g>'


def compact(value: int) -> str:
    """Stat values in GitHub's short form once they reach five digits: 10.4k, 1.2M."""
    if value < 10_000:
        return group_digits(value)
    if value < 999_950:
        return f"{value / 1_000:.1f}".removesuffix(".0") + "k"
    return f"{value / 1_000_000:.1f}".removesuffix(".0") + "M"


def nice_step(peak: int, intervals: int = 4) -> int:
    """A 1/2/5 tick step giving at most `intervals` gridlines up to `peak`."""
    raw = max(peak, 1) / intervals
    magnitude = 10 ** (len(str(int(raw))) - 1)
    for factor in (1, 2, 5, 10):
        if factor * magnitude >= raw:
            return factor * magnitude
    return 10 * magnitude


def column_path(x: float, top: float, width: float, bottom: float, radius: float = 4) -> str:
    """A column with a rounded data end and a square foot on the baseline."""
    r = min(radius, width / 2, bottom - top)
    return (
        f"M{x:.2f} {bottom:.2f}V{top + r:.2f}A{r:.2f} {r:.2f} 0 0 1 {x + r:.2f} {top:.2f}"
        f"H{x + width - r:.2f}A{r:.2f} {r:.2f} 0 0 1 {x + width:.2f} {top + r:.2f}"
        f"V{bottom:.2f}Z"
    )


def bar_path(left: float, y: float, length: float, height: float, radius: float = 4) -> str:
    """A horizontal bar with a square foot on the left baseline and a rounded tip."""
    r = min(radius, height / 2, length)
    right = left + length
    return (
        f"M{left:.2f} {y:.2f}H{right - r:.2f}A{r:.2f} {r:.2f} 0 0 1 {right:.2f} {y + r:.2f}"
        f"V{y + height - r:.2f}A{r:.2f} {r:.2f} 0 0 1 {right - r:.2f} {y + height:.2f}"
        f"H{left:.2f}Z"
    )


def render_stats_card(*, theme_name: str, stats: dict) -> str:
    theme = THEMES[theme_name]
    hero_y = 58
    row_y = 98
    height = 120

    # Private work is part of the total but not of the public counts below it,
    # so it is named in the same sentence as the total.
    private = f", {group_digits(stats['private'])} in private repositories" if stats["private"] else ""
    parts = [
        (
            f'  <text x="{PAD}" y="{hero_y}">'
            f'<tspan class="strong" style="font-size: 48px">{group_digits(stats["contributions"])}</tspan>'
            f'<tspan class="muted" style="font-size: 16px" dx="10">'
            f"contributions since {stats['since']}{private}</tspan>"
            "</text>"
        ),
    ]

    entries = []
    for icon, value, noun in (
        ("git-commit", stats["commits"], "commit"),
        ("git-pull-request", stats["prs"], "pull request"),
        ("issue-opened", stats["issues"], "issue"),
        ("star", stats["stars"], "star"),
    ):
        word = noun if value == 1 else f"{noun}s"
        number = compact(value)
        width = 24 + text_width(number, 14, bold=True) + 4 + text_width(word, 14)
        entries.append((icon, number, word, width))
    # Inline like a repository's metadata row: fixed gaps rather than columns,
    # tightened only if the values outgrow the card.
    gap = min(36.0, max(16.0, (CONTENT - sum(e[3] for e in entries)) / (len(entries) - 1)))
    x = float(PAD)
    for icon, number, word, width in entries:
        parts.append(octicon(icon, x, row_y - 13, theme["muted"]))
        parts.append(
            f'  <text x="{x + 24:.1f}" y="{row_y}"><tspan class="strong">{number}</tspan>'
            f'<tspan class="muted" dx="4">{word}</tspan></text>'
        )
        x += width + gap

    return svg_document(
        theme=theme,
        height=height,
        title="Contributions",
        description=(
            f"{stats['contributions']} contributions since {stats['since']}, "
            f"{stats['private']} of them in private repositories. {stats['commits']} commits, "
            f"{stats['prs']} pull requests, {stats['issues']} issues, {stats['stars']} stars."
        ),
        body="\n".join(parts),
    )


def render_top_languages_card(
    *, theme_name: str, languages: list[tuple[str, str, float]], repo_count: int
) -> str:
    """Ranked bars in GitHub's neutral progress grey, so close shares stay comparable
    on a shared baseline and the contribution green stays the page's only accent.

    GitHub's language colours are kept as a dot beside each name, where readers
    recognise them, but never carry identity: the palette was not built for
    charts and several neighbours collapse under colour vision deficiency.
    """
    theme = THEMES[theme_name]
    heading_y = 33
    row_y = 66
    row_step = 24
    bar_h = 8
    height = row_y + (len(languages) - 1) * row_step + 20

    name_width = max(text_width(name, 14, bold=True) for name, _, _ in languages)
    bar_x = PAD + 16 + name_width + 16
    value_room = text_width("100.0%", 14) + 8
    longest = CARD_WIDTH - PAD - value_room - bar_x
    peak = max(share for _, _, share in languages)

    parts = [
        f'  <text class="strong" x="{PAD}" y="{heading_y}" style="font-size: 16px">Languages</text>',
        (
            f'  <text class="muted" x="{CARD_WIDTH - PAD}" y="{heading_y}" text-anchor="end">'
            f"Across {plural(repo_count, 'repository', 'repositories')}</text>"
        ),
    ]
    for index, (name, color, share) in enumerate(languages):
        y = row_y + index * row_step
        length = max(2.0, share / peak * longest)
        parts.extend(
            [
                f'  <circle cx="{PAD + 4}" cy="{y - 5}" r="4" fill="{color}" stroke="{theme["ring"]}" />',
                f'  <text class="strong" x="{PAD + 16}" y="{y}">{escape(name)}</text>',
                f'  <path d="{bar_path(bar_x, y - 5 - bar_h / 2, length, bar_h)}" fill="{theme["share_bar"]}" />',
                f'  <text class="muted" x="{bar_x + length + 8:.1f}" y="{y}">{share * 100:.1f}%</text>',
            ]
        )

    return svg_document(
        theme=theme,
        height=height,
        title="Languages",
        description=f"Languages by bytes across {repo_count} repositories: "
        + ", ".join(f"{name} {share * 100:.1f}%" for name, _, share in languages)
        + ".",
        body="\n".join(parts),
    )


def render_activity_card(
    *, theme_name: str, streaks: Streaks, weeks: list[tuple[dt.date, int]]
) -> str:
    """A year of weekly totals in the contribution graph's own green."""
    theme = THEMES[theme_name]
    heading_y = 33
    plot_top = 72
    plot_bottom = 168
    plot_h = plot_bottom - plot_top
    month_y = plot_bottom + 20
    height = month_y + 20
    values = [total for _, total in weeks]
    peak = max(values)
    peak_index = values.index(peak) if peak else -1
    year_total = sum(values)

    # The plot tops out at the busiest week, which carries its own label; the
    # gridlines sit on clean values beneath it so they never crowd that label.
    ceiling = max(peak, 1)
    ticks = list(range(0, peak + 1, nice_step(peak))) if peak else [0]
    plot_left = PAD + max(text_width(group_digits(tick), 12) for tick in ticks) + 8
    gap = 3
    slot = (CARD_WIDTH - PAD - plot_left + gap) / len(weeks)
    bar_w = slot - gap

    def y_of(value: float) -> float:
        return plot_bottom - value / ceiling * plot_h

    def streak(label: str, days: int, dx: int = 0) -> str:
        shift = f' dx="{dx}"' if dx else ""
        return (
            f'<tspan class="muted"{shift}>{label}</tspan>'
            f'<tspan class="strong" dx="6">{plural(days, "day")}</tspan>'
        )

    parts = [
        (
            f'  <text x="{PAD}" y="{heading_y}" style="font-size: 16px">'
            f"{plural(year_total, 'contribution')} in the last year</text>"
        ),
        f'  <text x="{CARD_WIDTH - PAD}" y="{heading_y}" text-anchor="end">'
        + streak("Current streak", streaks.current_length)
        + streak("Longest streak", streaks.longest_length, dx=24)
        + "</text>",
    ]

    # Recessive value axis: solid hairlines one step off the surface.
    for tick in ticks:
        y = y_of(tick)
        parts.append(
            f'  <line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{CARD_WIDTH - PAD}" y2="{y:.1f}" '
            f'stroke="{theme["grid"]}" />'
        )
        parts.append(
            f'  <text class="small muted" x="{plot_left - 8:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="end" style="font-variant-numeric: tabular-nums">{group_digits(tick)}</text>'
        )

    for index, (_, value) in enumerate(weeks):
        if not value:
            continue
        x = plot_left + index * slot
        top = min(y_of(value), plot_bottom - 2)
        parts.append(f'  <path d="{column_path(x, top, bar_w, plot_bottom)}" fill="{theme["bar"]}" />')

    if peak_index >= 0:
        center = plot_left + peak_index * slot + bar_w / 2
        anchor = "middle"
        if center > CARD_WIDTH - PAD - 16:
            center, anchor = CARD_WIDTH - PAD, "end"
        parts.append(
            f'  <text class="small strong" x="{center:.1f}" y="{y_of(peak) - 6:.1f}" '
            f'text-anchor="{anchor}">{group_digits(peak)}</text>'
        )

    # Label a month on its first week, as the contribution calendar does.
    last_label_x = -1e9
    for index, (start, _) in enumerate(weeks):
        if index and start.month == weeks[index - 1][0].month:
            continue
        x = plot_left + index * slot
        if x - last_label_x < 32 or x > CARD_WIDTH - PAD - 24:
            continue
        if index == 0 and len(weeks) > 2 and weeks[2][0].month != start.month:
            continue
        parts.append(f'  <text class="small muted" x="{x:.1f}" y="{month_y}">{start:%b}</text>')
        last_label_x = x

    busiest_start = weeks[peak_index][0] if peak_index >= 0 else weeks[-1][0]
    return svg_document(
        theme=theme,
        height=height,
        title="Contribution activity",
        description=(
            f"{year_total} contributions in the last 52 weeks, shown as weekly totals. "
            f"Busiest week: {peak} contributions starting "
            f"{busiest_start:%B} {busiest_start.day}, {busiest_start.year}. "
            f"Current streak {streaks.current_length} days, "
            f"longest streak {streaks.longest_length} days."
        ),
        body="\n".join(parts),
    )


def parse_list(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", required=True, help="GitHub login to build the cards for")
    parser.add_argument("--out-dir", type=Path, default=Path("profile"))
    parser.add_argument("--langs-count", type=int, default=6)
    parser.add_argument("--hide-langs", default="", help="Comma separated language names to drop")
    parser.add_argument("--exclude-repos", default="", help="Comma separated repository names to drop")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        parser.error("GITHUB_TOKEN is not set")

    now = dt.datetime.now(dt.timezone.utc)
    profile = fetch_profile(token, args.login)
    created_at = dt.datetime.strptime(profile["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    repositories = fetch_repositories(token, args.login)
    contributions = fetch_contributions(token, args.login, created_at, now)
    write_cards(args, profile, created_at, repositories, contributions)
    return 0


def write_cards(
    args: argparse.Namespace,
    profile: dict,
    created_at: dt.datetime,
    repositories: list[Repository],
    contributions: dict,
) -> None:
    private = contributions["restricted"]
    streaks = compute_streaks(contributions["days"], private)
    weeks = weekly_series(contributions["days"])

    stars = sum(repository.stars for repository in repositories)
    issues = profile["openIssues"]["totalCount"] + profile["closedIssues"]["totalCount"]
    prs = profile["pullRequests"]["totalCount"]
    # The rank models overall activity, so private work counts toward it even
    # though the card reports it on its own column.
    rank_level, rank_percentile = calculate_rank(
        commits=contributions["commits"] + private,
        prs=prs,
        issues=issues,
        reviews=contributions["reviews"],
        stars=stars,
        followers=profile["followers"]["totalCount"],
    )
    stats = {
        "stars": stars,
        "commits": contributions["commits"],
        "private": private,
        "contributions": streaks.total_contributions,
        "prs": prs,
        "issues": issues,
        "since": f"{created_at:%B %Y}",
    }
    languages = aggregate_languages(
        repositories,
        hidden=parse_list(args.hide_langs),
        excluded=parse_list(args.exclude_repos),
        count=args.langs_count,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for variant in THEMES:
        (args.out_dir / f"stats-{variant}.svg").write_text(
            render_stats_card(theme_name=variant, stats=stats), encoding="utf-8"
        )
        (args.out_dir / f"top-langs-{variant}.svg").write_text(
            render_top_languages_card(
                theme_name=variant, languages=languages, repo_count=len(repositories)
            ),
            encoding="utf-8",
        )
        (args.out_dir / f"activity-{variant}.svg").write_text(
            render_activity_card(theme_name=variant, streaks=streaks, weeks=weeks),
            encoding="utf-8",
        )

    print(
        f"{profile['login']}: rank {rank_level} ({rank_percentile:.2f}%), {stars} stars, "
        f"{contributions['commits']} public commits, {prs} PRs, {issues} issues, "
        f"{streaks.total_contributions} contributions, "
        f"current streak {streaks.current_length}, longest streak {streaks.longest_length}, "
        f"busiest week {max(total for _, total in weeks)}"
    )
    if not private:
        print(
            "warning: restrictedContributionsCount is 0, so no private contributions were "
            "counted. Set PROFILE_TOKEN to a classic PAT with repo + read:user scope.",
            file=sys.stderr,
        )
    else:
        print(f"including {private} private contributions", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
