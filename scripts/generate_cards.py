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
import math
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

# Every card renders at one size so the README stack lines up. 495x195 is what
# the widest card (streak) needs; the others distribute their content into it.
CARD_WIDTH = 495
CARD_HEIGHT = 195
CARD_PADDING = 25
BODY_TOP = 55

# Ported from anuraghazra/github-readme-stats themes/index.js so the cards keep
# rendering with the palettes the README was built around.
CARD_THEMES = {
    "default": {
        "title": "#2f80ed",
        "icon": "#4c71f2",
        "text": "#434d58",
        "bg": "#fffefe",
        "border": "#e4e2e2",
        "ring": "#2f80ed",
    },
    "github_dark_dimmed": {
        "title": "#539bf5",
        "icon": "#539bf5",
        "text": "#ADBAC7",
        "bg": "#24292F",
        "border": "#373E47",
        "ring": "#539bf5",
    },
}

# Ported from DenverCoder1/github-readme-streak-stats.
STREAK_THEMES = {
    "default": {
        "bg": "#FFFEFE",
        "border": "#E4E2E2",
        "divider": "#E4E2E2",
        "ring": "#FB8C00",
        "fire": "#FB8C00",
        "side_num": "#151515",
        "side_label": "#151515",
        "curr_num": "#151515",
        "curr_label": "#FB8C00",
        "dates": "#464646",
    },
    "github_dark_dimmed": {
        "bg": "#24292F",
        "border": "#373E47",
        "divider": "#539BF5",
        "ring": "#539BF5",
        "fire": "#539BF5",
        "side_num": "#ADBAC7",
        "side_label": "#539BF5",
        "curr_num": "#ADBAC7",
        "curr_label": "#539BF5",
        "dates": "#ADBAC7",
    },
}

# Octicon paths, viewBox 0 0 16 16.
ICONS = {
    "stars": '<path fill-rule="evenodd" d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25zm0 2.445L6.615 5.5a.75.75 0 01-.564.41l-3.097.45 2.24 2.184a.75.75 0 01.216.664l-.528 3.084 2.769-1.456a.75.75 0 01.698 0l2.77 1.456-.53-3.084a.75.75 0 01.216-.664l2.24-2.183-3.096-.45a.75.75 0 01-.564-.41L8 2.694v.001z"/>',
    "commits": '<path fill-rule="evenodd" d="M1.643 3.143L.427 1.927A.25.25 0 000 2.104V5.75c0 .138.112.25.25.25h3.646a.25.25 0 00.177-.427L2.715 4.215a6.5 6.5 0 11-1.18 4.458.75.75 0 10-1.493.154 8.001 8.001 0 101.6-5.684zM7.75 4a.75.75 0 01.75.75v2.992l2.028.812a.75.75 0 01-.557 1.392l-2.5-1A.75.75 0 017 8.25v-3.5A.75.75 0 017.75 4z"/>',
    "prs": '<path fill-rule="evenodd" d="M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z"/>',
    "issues": '<path fill-rule="evenodd" d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm9 3a1 1 0 11-2 0 1 1 0 012 0zm-.25-6.25a.75.75 0 00-1.5 0v3.5a.75.75 0 001.5 0v-3.5z"/>',
    "contribs": '<path fill-rule="evenodd" d="M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 012 11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 12.25v3.25a.25.25 0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 00.4-.2v-3.25a.25.25 0 00-.25-.25h-3.5a.25.25 0 00-.25.25z"/>',
}

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    repositoriesContributedTo(
      first: 1
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
    ) { totalCount }
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
    """Collect every contribution day plus commit and review totals.

    contributionsCollection caps a query at one year, so this walks calendar
    years from account creation to now.
    """
    days: dict[str, int] = {}
    commits = 0
    reviews = 0
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
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]
    return {"days": days, "commits": commits, "reviews": reviews}


def compute_streaks(days: dict[str, int]) -> Streaks:
    """Fold the contribution calendar into total, current and longest streaks.

    The final day never breaks the current streak: the card is generated before
    the day is over.
    """
    if not days:
        raise GitHubError("Contribution calendar came back empty")

    ordered = sorted(days.items())
    today = dt.date.fromisoformat(ordered[-1][0])
    streaks = Streaks(
        total_contributions=0,
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
) -> list[tuple[str, str, int]]:
    totals: dict[str, list] = {}
    for repository in repositories:
        if repository.name.lower() in excluded:
            continue
        for name, color, size in repository.languages:
            if name.lower() in hidden:
                continue
            entry = totals.setdefault(name, [color, 0])
            entry[1] += size
    ranked = sorted(totals.items(), key=lambda item: item[1][1], reverse=True)
    return [(name, color, size) for name, (color, size) in ranked[:count]]


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


def k_format(value: int) -> str:
    if abs(value) <= 999:
        return str(value)
    scaled = round(abs(value) / 1000, 1)
    text = f"{scaled:.1f}".removesuffix(".0")
    return f"{'-' if value < 0 else ''}{text}k"


def group_digits(value: int) -> str:
    return f"{value:,}"


def format_date(date: dt.date, current_year: int) -> str:
    if date.year == current_year:
        return f"{date:%b} {date.day}"
    return f"{date:%b} {date.day}, {date.year}"


def format_range(start: dt.date, end: dt.date, current_year: int) -> str:
    if start == end:
        return format_date(start, current_year)
    return f"{format_date(start, current_year)} - {format_date(end, current_year)}"


def measure_text(text: str, font_size: float) -> float:
    """Rough advance width for 'Segoe UI'; only feeds column spacing decisions."""
    return len(text) * font_size * 0.6


def card_shell(
    *,
    width: int,
    height: int,
    theme: dict,
    title: str,
    description: str,
    style: str,
    content: str,
    overlay: str = "",
) -> str:
    """Shared frame: border, title, and a body group offset below the title."""
    return f"""<svg
  width="{width}"
  height="{height}"
  viewBox="0 0 {width} {height}"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
  role="img"
  aria-labelledby="titleId descId"
>
  <title id="titleId">{escape(title)}</title>
  <desc id="descId">{escape(description)}</desc>
  <style>
    .header {{
      font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme["title"]};
      animation: fadeInAnimation 0.8s ease-in-out forwards;
    }}
    @supports(-moz-appearance: auto) {{
      /* Selector detects Firefox */
      .header {{ font-size: 15.5px; }}
    }}
    .stat {{
      font: 600 14px 'Segoe UI', Ubuntu, "Helvetica Neue", Sans-Serif;
      fill: {theme["text"]};
    }}
    @supports(-moz-appearance: auto) {{
      .stat {{ font-size: 12px; }}
    }}
    .bold {{ font-weight: 700; }}
    .icon {{ fill: {theme["icon"]}; }}
    .stagger {{
      opacity: 0;
      animation: fadeInAnimation 0.3s ease-in-out forwards;
    }}
    @keyframes fadeInAnimation {{
      from {{ opacity: 0; }}
      to {{ opacity: 1; }}
    }}
{style}
  </style>
  <rect
    x="0.5"
    y="0.5"
    rx="4.5"
    width="{width - 1}"
    height="{height - 1}"
    fill="{theme["bg"]}"
    stroke="{theme["border"]}"
    stroke-opacity="1"
  />
  <g transform="translate(25, 35)">
    <text x="0" y="0" class="header">{escape(title)}</text>
  </g>
{overlay}
  <g transform="translate(0, 55)">
{content}
  </g>
</svg>
"""


def render_stats_card(*, theme_name: str, name: str, stats: dict) -> str:
    theme = CARD_THEMES[theme_name]
    rows = [
        ("stars", "Total Stars Earned", stats["stars"]),
        ("commits", "Total Commits", stats["commits"]),
        ("prs", "Total PRs", stats["prs"]),
        ("issues", "Total Issues", stats["issues"]),
        ("contribs", "Contributed to", stats["contributed_to"]),
    ]
    width, height = CARD_WIDTH, CARD_HEIGHT
    ring_x = width - 76.5
    # Spread the rows over the body so the block sits centred against the ring.
    body_height = height - BODY_TOP
    line_height = min(28, (body_height - 20) // len(rows))
    block_top = (body_height - (len(rows) - 1) * line_height - 14) / 2

    items = []
    for index, (key, label, value) in enumerate(rows):
        offset = block_top + index * line_height
        items.append(
            f"""    <g class="stagger" style="animation-delay: {(index + 3) * 150}ms" transform="translate({CARD_PADDING}, {offset:g})">
      <svg class="icon" viewBox="0 0 16 16" version="1.1" width="16" height="16">{ICONS[key]}</svg>
      <text class="stat bold" x="25" y="12.5">{label}:</text>
      <text class="stat bold" x="230" y="12.5">{k_format(value)}</text>
    </g>"""
        )

    # Rank ring fills clockwise; the lower the percentile the fuller the ring.
    circumference = 2 * math.pi * 40
    progress = max(0.0, min(100.0, 100 - stats["rank_percentile"]))
    dash_offset = (100 - progress) / 100 * circumference

    style = f"""    .rank-text {{
      font: 800 24px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme["text"]};
      animation: scaleInAnimation 0.3s ease-in-out forwards;
    }}
    @keyframes scaleInAnimation {{
      from {{ transform: translate(-5px, 5px) scale(0); }}
      to {{ transform: translate(-5px, 5px) scale(1); }}
    }}
    .rank-circle-rim {{
      stroke: {theme["ring"]};
      fill: none;
      stroke-width: 6;
      opacity: 0.2;
    }}
    .rank-circle {{
      stroke: {theme["ring"]};
      stroke-dasharray: 250;
      fill: none;
      stroke-width: 6;
      stroke-linecap: round;
      opacity: 0.8;
      transform-origin: -10px 8px;
      transform: rotate(-90deg);
      animation: rankAnimation 1s forwards ease-in-out;
    }}
    @keyframes rankAnimation {{
      from {{ stroke-dashoffset: {circumference:.2f}; }}
      to {{ stroke-dashoffset: {dash_offset:.2f}; }}
    }}"""

    overlay = f"""  <g transform="translate({ring_x:g}, {height / 2 - 50:g})">
    <circle class="rank-circle-rim" cx="-10" cy="8" r="40" />
    <circle class="rank-circle" cx="-10" cy="8" r="40" />
    <g class="rank-text">
      <text x="-5" y="3" dominant-baseline="central" text-anchor="middle">{stats["rank_level"]}</text>
    </g>
  </g>"""

    description = ", ".join(f"{label}: {value}" for _, label, value in rows)
    return card_shell(
        width=width,
        height=height,
        theme=theme,
        title=f"{name}'{'' if name.strip().lower().endswith('s') else 's'} GitHub Stats",
        description=f"{description}, Rank: {stats['rank_level']}",
        style=style,
        content="\n".join(items),
        overlay=overlay,
    )


def render_top_languages_card(*, theme_name: str, languages: list[tuple[str, str, int]]) -> str:
    theme = CARD_THEMES[theme_name]
    if not languages:
        raise GitHubError("No languages left after applying the hide and exclude filters")

    width, height = CARD_WIDTH, CARD_HEIGHT
    bar_width = width - 2 * CARD_PADDING
    total_size = sum(size for _, _, size in languages)

    # Fit the legend to the card: as many columns as the widest entry allows,
    # then centre the resulting block in the space left under the bar.
    longest = max(languages, key=lambda language: len(language[0]))
    entry_width = 35 + measure_text(f"{longest[0]} {longest[2] / total_size * 100:.2f}%", 11)
    column_count = max(1, min(3, len(languages), int(bar_width // entry_width)))
    rows_per_column = -(-len(languages) // column_count)
    column_gap = bar_width / column_count

    body_height = height - BODY_TOP
    row_gap = 30 if rows_per_column < 2 else min(30, max(16, (body_height - 40) // (rows_per_column - 1)))
    legend_top = 8 + (body_height - 8 - ((rows_per_column - 1) * row_gap + 12)) / 2

    bars = []
    offset = 0.0
    for _, color, size in languages:
        share = round(size / total_size * bar_width, 2)
        # Slivers below 10px get padded so their rounded cap stays visible.
        bars.append(
            f"""      <rect
        mask="url(#rect-mask)"
        x="{offset:g}"
        y="0"
        width="{share + 10 if share < 10 else share:g}"
        height="8"
        fill="{color}"
      />"""
        )
        offset = round(offset + share, 2)

    newline = "\n"
    columns = []
    for column_index in range(column_count):
        chunk = languages[column_index * rows_per_column : (column_index + 1) * rows_per_column]
        entries = []
        for row_index, (name, color, size) in enumerate(chunk):
            entries.append(
                f"""        <g transform="translate(0, {row_index * row_gap:g})">
          <g class="stagger" style="animation-delay: {(row_index + 3) * 150}ms">
            <circle cx="5" cy="6" r="5" fill="{color}" />
            <text x="15" y="10" class="lang-name">{escape(name)} {size / total_size * 100:.2f}%</text>
          </g>
        </g>"""
            )
        if entries:
            columns.append(
                f"""      <g transform="translate({column_index * column_gap:g}, 0)">
{newline.join(entries)}
      </g>"""
            )

    style = f"""    .lang-name {{
      font: 400 11px "Segoe UI", Ubuntu, Sans-Serif;
      fill: {theme["text"]};
    }}
    #rect-mask rect {{ animation: slideInAnimation 1s ease-in-out forwards; }}
    @keyframes slideInAnimation {{
      from {{ width: 0; }}
      to {{ width: {bar_width}px; }}
    }}"""

    content = f"""    <svg x="{CARD_PADDING}">
      <mask id="rect-mask">
        <rect x="0" y="0" width="{bar_width}" height="8" fill="white" rx="5" />
      </mask>
{newline.join(bars)}
      <g transform="translate(0, {legend_top:g})">
{newline.join(columns)}
      </g>
    </svg>"""

    description = ", ".join(f"{name} {size / total_size * 100:.2f}%" for name, _, size in languages)
    return card_shell(
        width=width,
        height=height,
        theme=theme,
        title="Most Used Languages",
        description=description,
        style=style,
        content=content,
    )


def render_streak_card(*, theme_name: str, streaks: Streaks) -> str:
    theme = STREAK_THEMES[theme_name]
    year = streaks.today.year
    total_range = f"{format_date(streaks.first_contribution, year)} - Present"
    current_range = (
        format_range(streaks.current_start, streaks.current_end, year)
        if streaks.current_length
        else format_date(streaks.today, year)
    )
    longest_range = (
        format_range(streaks.longest_start, streaks.longest_end, year)
        if streaks.longest_length
        else format_date(streaks.today, year)
    )
    font = '"Segoe UI", Ubuntu, sans-serif'

    def text(
        x: float, y: float, dy: int, fill: str, weight: int, size: int, animation: str, value: str
    ) -> str:
        return f"""      <g transform="translate({x}, {y})">
        <text x="0" y="{dy}" text-anchor="middle" stroke="none" stroke-width="0" fill="{fill}" font-family='{font}' font-weight="{weight}" font-size="{size}px" style="{animation}">{escape(value)}</text>
      </g>"""

    def fade(delay: float) -> str:
        return f"opacity: 0; animation: fadein 0.5s linear forwards {delay}s"

    return f"""<svg
  xmlns="http://www.w3.org/2000/svg"
  style="isolation: isolate"
  viewBox="0 0 495 195"
  width="495px"
  height="195px"
  direction="ltr"
  role="img"
  aria-labelledby="titleId descId"
>
  <title id="titleId">GitHub contribution streak</title>
  <desc id="descId">Total contributions: {group_digits(streaks.total_contributions)}, current streak: {streaks.current_length} days, longest streak: {streaks.longest_length} days</desc>
  <style>
    @keyframes currstreak {{
      0% {{ font-size: 3px; opacity: 0.2; }}
      80% {{ font-size: 34px; opacity: 1; }}
      100% {{ font-size: 28px; opacity: 1; }}
    }}
    @keyframes fadein {{
      0% {{ opacity: 0; }}
      100% {{ opacity: 1; }}
    }}
  </style>
  <defs>
    <clipPath id="outer_rectangle">
      <rect width="495" height="195" rx="4.5" />
    </clipPath>
    <mask id="mask_out_ring_behind_fire">
      <rect width="495" height="195" fill="white" />
      <ellipse cx="247.5" cy="32" rx="13" ry="18" fill="black" />
    </mask>
  </defs>
  <g clip-path="url(#outer_rectangle)">
    <rect x="0.5" y="0.5" width="494" height="194" rx="4.5" fill="{theme["bg"]}" stroke="{theme["border"]}" />
    <line x1="165" y1="28" x2="165" y2="170" vector-effect="non-scaling-stroke" stroke-width="1" stroke="{theme["divider"]}" stroke-linecap="square" stroke-miterlimit="3" />
    <line x1="330" y1="28" x2="330" y2="170" vector-effect="non-scaling-stroke" stroke-width="1" stroke="{theme["divider"]}" stroke-linecap="square" stroke-miterlimit="3" />
    <g>
{text(82.5, 48, 32, theme["side_num"], 700, 28, fade(0.6), group_digits(streaks.total_contributions))}
{text(82.5, 84, 32, theme["side_label"], 400, 14, fade(0.7), "Total Contributions")}
{text(82.5, 114, 32, theme["dates"], 400, 12, fade(0.8), total_range)}
    </g>
    <g>
      <g mask="url(#mask_out_ring_behind_fire)">
        <circle cx="247.5" cy="71" r="40" fill="none" stroke="{theme["ring"]}" stroke-width="5" style="{fade(0.4)}" />
      </g>
      <g transform="translate(247.5, 19.5)" stroke-opacity="0" style="{fade(0.6)}">
        <path d="M -12 -0.5 L 15 -0.5 L 15 23.5 L -12 23.5 L -12 -0.5 Z" fill="none" />
        <path d="M 1.5 0.67 C 1.5 0.67 2.24 3.32 2.24 5.47 C 2.24 7.53 0.89 9.2 -1.17 9.2 C -3.23 9.2 -4.79 7.53 -4.79 5.47 L -4.76 5.11 C -6.78 7.51 -8 10.62 -8 13.99 C -8 18.41 -4.42 22 0 22 C 4.42 22 8 18.41 8 13.99 C 8 8.6 5.41 3.79 1.5 0.67 Z M -0.29 19 C -2.07 19 -3.51 17.6 -3.51 15.86 C -3.51 14.24 -2.46 13.1 -0.7 12.74 C 1.07 12.38 2.9 11.53 3.92 10.16 C 4.31 11.45 4.51 12.81 4.51 14.2 C 4.51 16.85 2.36 19 -0.29 19 Z" fill="{theme["fire"]}" stroke-opacity="0" />
      </g>
{text(247.5, 48, 32, theme["curr_num"], 700, 28, "animation: currstreak 0.6s linear forwards", str(streaks.current_length))}
{text(247.5, 108, 32, theme["curr_label"], 700, 14, fade(0.9), "Current Streak")}
{text(247.5, 145, 21, theme["dates"], 400, 12, fade(0.9), current_range)}
    </g>
    <g>
{text(412.5, 48, 32, theme["side_num"], 700, 28, fade(1.2), str(streaks.longest_length))}
{text(412.5, 84, 32, theme["side_label"], 400, 14, fade(1.3), "Longest Streak")}
{text(412.5, 114, 32, theme["dates"], 400, 12, fade(1.4), longest_range)}
    </g>
  </g>
</svg>
"""


def parse_list(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", required=True, help="GitHub login to build the cards for")
    parser.add_argument("--out-dir", type=Path, default=Path("profile"))
    parser.add_argument("--dark-theme", default="github_dark_dimmed", choices=sorted(CARD_THEMES))
    parser.add_argument("--light-theme", default="default", choices=sorted(CARD_THEMES))
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
    streaks = compute_streaks(contributions["days"])

    stars = sum(repository.stars for repository in repositories)
    issues = profile["openIssues"]["totalCount"] + profile["closedIssues"]["totalCount"]
    prs = profile["pullRequests"]["totalCount"]
    rank_level, rank_percentile = calculate_rank(
        commits=contributions["commits"],
        prs=prs,
        issues=issues,
        reviews=contributions["reviews"],
        stars=stars,
        followers=profile["followers"]["totalCount"],
    )
    stats = {
        "stars": stars,
        "commits": contributions["commits"],
        "prs": prs,
        "issues": issues,
        "contributed_to": profile["repositoriesContributedTo"]["totalCount"],
        "rank_level": rank_level,
        "rank_percentile": rank_percentile,
    }
    languages = aggregate_languages(
        repositories,
        hidden=parse_list(args.hide_langs),
        excluded=parse_list(args.exclude_repos),
        count=args.langs_count,
    )
    name = profile["name"] or profile["login"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    variants = {"dark": args.dark_theme, "light": args.light_theme}
    for variant, theme_name in variants.items():
        (args.out_dir / f"stats-{variant}.svg").write_text(
            render_stats_card(theme_name=theme_name, name=name, stats=stats), encoding="utf-8"
        )
        (args.out_dir / f"top-langs-{variant}.svg").write_text(
            render_top_languages_card(theme_name=theme_name, languages=languages), encoding="utf-8"
        )
        (args.out_dir / f"streak-{variant}.svg").write_text(
            render_streak_card(theme_name=theme_name, streaks=streaks), encoding="utf-8"
        )

    print(
        f"{name}: rank {rank_level} ({rank_percentile:.2f}%), {stars} stars, "
        f"{contributions['commits']} commits, {prs} PRs, {issues} issues, "
        f"{streaks.total_contributions} contributions, "
        f"current streak {streaks.current_length}, longest streak {streaks.longest_length}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
