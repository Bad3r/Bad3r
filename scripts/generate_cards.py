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


# Layout. One size for every card so the README stack reads as one object.
# Each card carries a single hero, a short supporting row, and the data strip
# fused to its bottom edge, so the whole thing resolves in one glance.
CARD_WIDTH = 495
CARD_HEIGHT = 195
PAD = 24
CONTENT = CARD_WIDTH - 2 * PAD
HEADER_Y = 31
STRIP_H = 5
STRIP_Y = CARD_HEIGHT - STRIP_H
HERO_Y = 100
HERO_SIZE = 44
SUB_SIZE = 21
LABEL_SIZE = 11
ROW_Y = 150

# Monospace advance width as a fraction of the em, used to lay columns out
# without a text measurement pass.
ADVANCE = 0.6

# Identifiers, numerals and units are set in mono; whole sentences are set in
# the sans face. The face is the tell for whether a string is data or prose.
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"

# Palette built off #7e7eff, GitHub's assigned colour for Nix, which is this
# account's dominant language. The ground leans indigo rather than neutral so
# the cards do not read as the stock GitHub surface.
THEMES = {
    "dark": {
        "bg": "#12111C",
        "border": "#2C2A42",
        "rule": "#272442",
        "text": "#E5E2F6",
        "dim": "#8B86AB",
        "accent": "#8080FF",
        "track": "#232038",
        "ridge": "0.2",
    },
    "light": {
        "bg": "#F6F5FC",
        "border": "#DEDBF1",
        "rule": "#E2DFF3",
        "text": "#191730",
        "dim": "#605B84",
        "accent": "#5654E0",
        "track": "#DCD8F0",
        "ridge": "0.14",
    },
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


def group_digits(value: int) -> str:
    return f"{value:,}"


def relative_luminance(color: str) -> float:
    channels = []
    for offset in (1, 3, 5):
        value = int(color[offset : offset + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def readable_on(color: str, background: str, *, fallback: str) -> str:
    """GitHub's language colours are picked for neither of these grounds.

    A colour that cannot clear 3:1 against the card would leave the headline
    unreadable on one of the two themes, so it drops back to the accent.
    """
    if len(color) != 7 or not color.startswith("#"):
        return fallback
    try:
        pair = sorted((relative_luminance(color), relative_luminance(background)))
    except ValueError:
        return fallback
    return color if (pair[1] + 0.05) / (pair[0] + 0.05) >= 3.0 else fallback


def weekly_series(days: dict[str, int], weeks: int = 52) -> list[int]:
    """Contribution totals per week for the most recent `weeks` weeks."""
    ordered = sorted(days.items())
    end = dt.date.fromisoformat(ordered[-1][0])
    start = end - dt.timedelta(days=weeks * 7 - 1)
    buckets = [0] * weeks
    for iso_date, count in ordered:
        index = (dt.date.fromisoformat(iso_date) - start).days // 7
        if 0 <= index < weeks:
            buckets[index] += count
    return buckets


def card_shell(
    *,
    theme: dict,
    path: str,
    meta: str,
    meta_prose: bool = False,
    hero: str,
    strip: str,
    body: str,
    style: str,
    description: str,
) -> str:
    """Every card is one attribute of the same set: path, hero, strip.

    The strip is fused to the bottom edge and clipped to the card silhouette,
    which keeps the proportion reading out of the content area entirely.
    """
    return f"""<svg
  width="{CARD_WIDTH}"
  height="{CARD_HEIGHT}"
  viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
  role="img"
  aria-labelledby="titleId descId"
>
  <title id="titleId">{escape(path)}</title>
  <desc id="descId">{escape(description)}</desc>
  <style>
    text {{ font-family: {MONO}; }}
    .path {{ font-size: 11.5px; font-weight: 500; fill: {theme["accent"]}; }}
    .meta {{ font-size: 11.5px; font-weight: 500; fill: {theme["dim"]}; }}
    .prose {{ font-family: {SANS}; font-size: 11.5px; font-weight: 400; fill: {theme["dim"]}; }}
    .hero {{
      font-size: {HERO_SIZE}px;
      font-weight: 600;
      letter-spacing: -0.035em;
      fill: {theme["text"]};
    }}
    .hero-key {{ font-size: 12.5px; font-weight: 500; fill: {theme["text"]}; }}
    .num {{
      font-size: {SUB_SIZE}px;
      font-weight: 600;
      letter-spacing: -0.02em;
      fill: {theme["text"]};
    }}
    .key {{ font-size: {LABEL_SIZE}px; font-weight: 500; fill: {theme["dim"]}; }}
    .item {{ font-size: 12.5px; font-weight: 500; fill: {theme["text"]}; }}
    .item-dim {{ font-size: 12.5px; font-weight: 500; fill: {theme["dim"]}; }}
    .in {{ opacity: 0; animation: in 0.45s ease-out forwards; }}
    @keyframes in {{
      from {{ opacity: 0; transform: translateY(4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes bleed {{
      from {{ width: 0; }}
      to {{ width: {CARD_WIDTH}px; }}
    }}
    .bleed {{ animation: bleed 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) 0.15s forwards; }}
    @media (prefers-reduced-motion: reduce) {{
      .in {{ animation: none; opacity: 1; }}
      .bleed {{ animation: none; }}
    }}
{style}
  </style>
  <clipPath id="card">
    <rect x="0" y="0" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="8" />
  </clipPath>
  <rect
    x="0.5"
    y="0.5"
    width="{CARD_WIDTH - 1}"
    height="{CARD_HEIGHT - 1}"
    rx="7.5"
    fill="{theme["bg"]}"
    stroke="{theme["border"]}"
  />
  <text class="path in" x="{PAD}" y="{HEADER_Y}">{path}</text>
  <text
    class="{"prose" if meta_prose else "meta"} in"
    x="{CARD_WIDTH - PAD}"
    y="{HEADER_Y}"
    text-anchor="end"
    style="animation-delay: 70ms"
  >{escape(meta)}</text>
{hero}
{body}
  <clipPath id="strip">
    <rect class="bleed" x="0" y="{STRIP_Y}" width="{CARD_WIDTH}" height="{STRIP_H}" />
  </clipPath>
  <g clip-path="url(#card)">
    <g clip-path="url(#strip)">
{strip}
    </g>
  </g>
</svg>
"""


def attribute_path(login: str, attribute: str) -> str:
    """Card titles read as one attribute set, in the grammar of the top language.

    The login is lowercased to sit in that grammar and to match the dimmer
    namespace tone; GitHub logins are case insensitive, so it still resolves.
    """
    return f'<tspan style="opacity: 0.62">{escape(login.lower())}.</tspan>{escape(attribute)}'


def strip_shares(segments: list[tuple[str, float]]) -> str:
    """Proportion reading across the full card width. Segments are (colour, share)."""
    parts = []
    offset = 0.0
    for color, share in segments:
        width = share * CARD_WIDTH
        parts.append(
            f'      <rect x="{offset:.2f}" y="{STRIP_Y}" width="{width:.2f}" '
            f'height="{STRIP_H}" fill="{color}" />'
        )
        offset += width
    return "\n".join(parts)


def strip_heat(values: list[int], theme: dict) -> str:
    """The same series the ridge draws, re-read as discrete weekly intensity."""
    peak = max(values) or 1
    cell = CARD_WIDTH / len(values)
    parts = [
        f'      <rect x="0" y="{STRIP_Y}" width="{CARD_WIDTH}" height="{STRIP_H}" fill="{theme["track"]}" />'
    ]
    for index, value in enumerate(values):
        if value <= 0:
            continue
        opacity = (0.3, 0.5, 0.72, 1.0)[min(3, int(value / peak * 3.999))]
        parts.append(
            f'      <rect x="{index * cell:.2f}" y="{STRIP_Y}" width="{cell - 1.2:.2f}" '
            f'height="{STRIP_H}" fill="{theme["accent"]}" fill-opacity="{opacity}" />'
        )
    return "\n".join(parts)


def hero_lockup(value: str, key: str, prose: str) -> str:
    """Headline numeral with its name set beside it rather than beneath it.

    Number and meaning land on one reading line, which is what makes the card
    resolve before the eye reaches the supporting row.
    """
    key_x = PAD + len(value) * HERO_SIZE * ADVANCE + 18
    return f"""  <g class="in" style="animation-delay: 140ms">
    <text class="hero" x="{PAD}" y="{HERO_Y}">{escape(value)}</text>
    <text class="hero-key" x="{key_x:.1f}" y="{HERO_Y - 12}">{escape(key)}</text>
    <text class="prose" x="{key_x:.1f}" y="{HERO_Y + 3}">{escape(prose)}</text>
  </g>"""


def stat_row(entries: list[tuple[str, str]], delay: int = 0) -> str:
    """Supporting numerals on a shared baseline, each named by its attribute.

    Columns are sized to their own content and the slack is split into even
    gutters, so a wide value like 10,066 never crowds its neighbour.
    """
    widths = [
        max(len(value) * SUB_SIZE, len(key) * LABEL_SIZE) * ADVANCE for value, key in entries
    ]
    gutter = (CONTENT - sum(widths)) / max(1, len(entries) - 1)
    blocks = []
    x = float(PAD)
    for index, (value, key) in enumerate(entries):
        blocks.append(
            f"""  <g class="in" style="animation-delay: {delay + index * 60}ms">
    <text class="num" x="{x:.1f}" y="{ROW_Y}">{escape(value)}</text>
    <text class="key" x="{x:.1f}" y="{ROW_Y + 16}">{escape(key)}</text>
  </g>"""
        )
        x += widths[index] + gutter
    return "\n".join(blocks)


def render_stats_card(*, theme_name: str, login: str, stats: dict) -> str:
    theme = THEMES[theme_name]
    public = stats["contributions"] - stats["private"]
    public_share = public / stats["contributions"] if stats["contributions"] else 1.0

    return card_shell(
        theme=theme,
        path=attribute_path(login, "contributions"),
        # The strip draws the public split, so the private figure only needs naming.
        meta=f"{group_digits(stats['private'])} private · rank {stats['rank_level']}",
        hero=hero_lockup(
            group_digits(stats["contributions"]), "contributions", f"since {stats['since']}"
        ),
        body=stat_row(
            [
                (group_digits(stats["commits"]), "commits"),
                (group_digits(stats["prs"]), "pullRequests"),
                (group_digits(stats["issues"]), "issues"),
                (group_digits(stats["stars"]), "stars"),
            ],
            delay=240,
        ),
        strip=strip_shares([(theme["accent"], public_share), (theme["track"], 1 - public_share)]),
        style="",
        description=(
            f"{stats['contributions']} contributions since {stats['since']}, "
            f"{public_share * 100:.0f}% of them public. {stats['commits']} public commits, "
            f"{stats['private']} private contributions, {stats['prs']} pull requests, "
            f"{stats['issues']} issues, {stats['stars']} stars, rank {stats['rank_level']}."
        ),
    )


def render_top_languages_card(
    *, theme_name: str, login: str, languages: list[tuple[str, str, int]], repo_count: int
) -> str:
    theme = THEMES[theme_name]
    if not languages:
        raise GitHubError("No languages left after applying the hide and exclude filters")

    total = sum(size for _, _, size in languages)
    shares = [(name, color, size / total) for name, color, size in languages]
    lead_name, lead_color, lead_share = shares[0]

    # The headline wears the language's own colour when the ground allows it.
    tint = readable_on(lead_color, theme["bg"], fallback=theme["accent"])
    # Long names step down until they clear the ranked list beside them.
    size = max(24.0, min(float(HERO_SIZE), 206 / max(1, len(lead_name) * ADVANCE)))
    percent = f"{lead_share * 100:.1f}%"
    hero = f"""  <g class="in" style="animation-delay: 140ms">
    <text
      class="hero"
      x="{PAD}"
      y="108"
      style="font-size: {size:.1f}px; fill: {tint}"
    >{escape(lead_name)}</text>
    <text class="hero-key" x="{PAD}" y="132">{percent}</text>
    <text class="prose" x="{PAD + len(percent) * 12.5 * ADVANCE + 9:.1f}" y="132">of all bytes written</text>
  </g>"""

    rows = []
    for index, (name, color, share) in enumerate(shares[1:]):
        y = 66 + index * 26
        rows.append(
            f"""  <g class="in" style="animation-delay: {260 + index * 60}ms">
    <circle cx="{PAD + 228}" cy="{y - 4}" r="4" fill="{color}" />
    <text class="item" x="{PAD + 244}" y="{y}">{escape(name)}</text>
    <text class="item-dim" x="{CARD_WIDTH - PAD}" y="{y}" text-anchor="end">{share * 100:.1f}%</text>
  </g>"""
        )

    return card_shell(
        theme=theme,
        path=attribute_path(login, "languages"),
        meta=f"{repo_count} repos · by bytes",
        hero=hero,
        body="\n".join(rows),
        strip=strip_shares([(color, share) for _, color, share in shares]),
        style="",
        description="Most used languages by bytes written: "
        + ", ".join(f"{name} {share * 100:.1f}%" for name, _, share in shares),
    )


def render_activity_card(*, theme_name: str, login: str, streaks: Streaks, weeks: list[int]) -> str:
    """The signature card: a year of real per-week data the numbers alone hide."""
    theme = THEMES[theme_name]
    accent = theme["accent"]
    top, height = 54, 58
    base = top + height
    peak = max(weeks) or 1
    step = CONTENT / (len(weeks) - 1)
    points = [(PAD + index * step, base - value / peak * height) for index, value in enumerate(weeks)]
    line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area = f"{line} L {PAD + CONTENT:.1f} {base} L {PAD} {base} Z"
    last_x, last_y = points[-1]

    style = f"""    .ridge-fill {{ fill: {accent}; opacity: {theme["ridge"]}; }}
    .ridge-line {{ stroke: {accent}; stroke-width: 1.6; fill: none;
                   stroke-linejoin: round; stroke-linecap: round; }}
    @keyframes sweep {{
      from {{ width: 0; }}
      to {{ width: {CONTENT}px; }}
    }}
    .sweep {{ animation: sweep 1s cubic-bezier(0.2, 0.8, 0.2, 1) forwards; }}
    @media (prefers-reduced-motion: reduce) {{
      .sweep {{ animation: none; }}
    }}"""

    hero = f"""  <clipPath id="ridge">
    <rect class="sweep" x="{PAD}" y="{top}" width="{CONTENT}" height="{height + 1}" />
  </clipPath>
  <g clip-path="url(#ridge)">
    <path class="ridge-fill" d="{area}" />
    <path class="ridge-line" d="{line}" />
  </g>
  <circle
    class="in"
    cx="{last_x:.1f}"
    cy="{last_y:.1f}"
    r="3.2"
    fill="{accent}"
    style="animation-delay: 950ms"
  />
  <line x1="{PAD}" y1="{base}" x2="{CARD_WIDTH - PAD}" y2="{base}" stroke="{theme["rule"]}" />"""

    return card_shell(
        theme=theme,
        path=attribute_path(login, "activity"),
        meta="last 52 weeks",
        meta_prose=True,
        hero=hero,
        body=stat_row(
            [
                (str(streaks.current_length), "currentStreak"),
                (str(streaks.longest_length), "longestStreak"),
                (group_digits(peak), "busiestWeek"),
            ],
            delay=300,
        ),
        strip=strip_heat(weeks, theme),
        style=style,
        description=(
            f"Weekly contributions over the last 52 weeks. Current streak "
            f"{streaks.current_length} days, longest streak {streaks.longest_length} days, "
            f"busiest week {peak} contributions."
        ),
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
        "rank_level": rank_level,
        "rank_percentile": rank_percentile,
        "since": f"{created_at:%b %Y}",
    }
    languages = aggregate_languages(
        repositories,
        hidden=parse_list(args.hide_langs),
        excluded=parse_list(args.exclude_repos),
        count=args.langs_count,
    )
    login = profile["login"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for variant in THEMES:
        (args.out_dir / f"stats-{variant}.svg").write_text(
            render_stats_card(theme_name=variant, login=login, stats=stats), encoding="utf-8"
        )
        (args.out_dir / f"top-langs-{variant}.svg").write_text(
            render_top_languages_card(
                theme_name=variant, login=login, languages=languages, repo_count=len(repositories)
            ),
            encoding="utf-8",
        )
        (args.out_dir / f"activity-{variant}.svg").write_text(
            render_activity_card(theme_name=variant, login=login, streaks=streaks, weeks=weeks),
            encoding="utf-8",
        )

    print(
        f"{login}: rank {rank_level} ({rank_percentile:.2f}%), {stars} stars, "
        f"{contributions['commits']} public commits, {prs} PRs, {issues} issues, "
        f"{streaks.total_contributions} contributions, "
        f"current streak {streaks.current_length}, longest streak {streaks.longest_length}, "
        f"busiest week {max(weeks)}"
    )
    if not private:
        print(
            "warning: restrictedContributionsCount is 0, so no private contributions were "
            "counted. Set PROFILE_TOKEN to a classic PAT with repo + read:user scope.",
            file=sys.stderr,
        )
    else:
        print(f"including {private} private contributions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
