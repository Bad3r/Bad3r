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
CARD_WIDTH = 495
CARD_HEIGHT = 195
PAD = 22
CONTENT = CARD_WIDTH - 2 * PAD
RULE_Y = 38

MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

# Palette built off #7e7eff, GitHub's assigned colour for Nix, which is this
# account's dominant language. The ground leans indigo rather than neutral so
# the cards do not read as the stock GitHub surface.
THEMES = {
    "dark": {
        "bg": "#14131F",
        "border": "#2E2C44",
        "rule": "#282640",
        "text": "#D7D4EA",
        "dim": "#7C7899",
        "accent": "#7E7EFF",
        "track": "#26243A",
    },
    "light": {
        "bg": "#F7F6FD",
        "border": "#DFDCF0",
        "rule": "#E3E0F2",
        "text": "#211E34",
        "dim": "#6B6690",
        "accent": "#5D5DE6",
        "track": "#D5D0EC",
    },
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
    *, theme: dict, eyebrow_left: str, eyebrow_right: str, body: str, style: str, description: str
) -> str:
    """Every card is a section of the same manifest: eyebrow, rule, content."""
    return f"""<svg
  width="{CARD_WIDTH}"
  height="{CARD_HEIGHT}"
  viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
  role="img"
  aria-labelledby="titleId descId"
>
  <title id="titleId">{escape(eyebrow_left)}</title>
  <desc id="descId">{escape(description)}</desc>
  <style>
    text {{ font-family: {MONO}; }}
    .key {{
      font-size: 8.5px;
      font-weight: 500;
      letter-spacing: 0.13em;
      fill: {theme["dim"]};
    }}
    .key-on {{ fill: {theme["accent"]}; }}
    .num {{
      font-size: 24px;
      font-weight: 600;
      letter-spacing: -0.02em;
      fill: {theme["text"]};
    }}
    .val {{ font-size: 11px; font-weight: 500; fill: {theme["text"]}; }}
    .val-dim {{ font-size: 11px; font-weight: 500; fill: {theme["dim"]}; }}
    .in {{ opacity: 0; animation: in 0.5s ease-out forwards; }}
    @keyframes in {{
      from {{ opacity: 0; transform: translateY(5px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes wipe {{
      from {{ width: 0; }}
      to {{ width: {CONTENT}px; }}
    }}
    .wipe {{ animation: wipe 0.85s cubic-bezier(0.2, 0.8, 0.2, 1) forwards; }}
    @media (prefers-reduced-motion: reduce) {{
      .in {{ animation: none; opacity: 1; }}
      .wipe {{ animation: none; }}
    }}
{style}
  </style>
  <rect
    x="0.5"
    y="0.5"
    width="{CARD_WIDTH - 1}"
    height="{CARD_HEIGHT - 1}"
    rx="7.5"
    fill="{theme["bg"]}"
    stroke="{theme["border"]}"
  />
  <text class="key key-on in" x="{PAD}" y="27">{escape(eyebrow_left)}</text>
  <text class="key in" x="{CARD_WIDTH - PAD}" y="27" text-anchor="end" style="animation-delay: 80ms">{escape(eyebrow_right)}</text>
  <line x1="{PAD}" y1="{RULE_Y}" x2="{CARD_WIDTH - PAD}" y2="{RULE_Y}" stroke="{theme["rule"]}" />
{body}
</svg>
"""


def measure_bar(segments: list[tuple[str, float]], y: int, height: int = 7) -> str:
    """Proportion band, the shared device across the set. Segments are (colour, share)."""
    parts = []
    offset = 0.0
    for color, share in segments:
        width = share * CONTENT
        parts.append(
            f'      <rect x="{PAD + offset:.2f}" y="{y}" width="{width:.2f}" '
            f'height="{height}" fill="{color}" />'
        )
        offset += width
    joined = "\n".join(parts)
    return f"""    <clipPath id="wipe">
      <rect class="wipe" x="{PAD}" y="{y}" width="{CONTENT}" height="{height}" rx="{height / 2}" />
    </clipPath>
    <g clip-path="url(#wipe)">
{joined}
    </g>"""


NUM_SIZE = 24
KEY_SIZE = 8.5


def stat_columns(entries: list[tuple[str, str]], y: int, delay: int = 0) -> str:
    """Numerals on a shared baseline with a tracked key beneath each.

    Columns are sized to their own content and the slack is split into even
    gutters, so a wide value like 10,066 never crowds its neighbour.
    """
    widths = [max(len(value) * NUM_SIZE * 0.6, len(key) * KEY_SIZE * 0.6 * 1.13) for value, key in entries]
    gutter = (CONTENT - sum(widths)) / max(1, len(entries) - 1)
    blocks = []
    x = float(PAD)
    for index, (value, key) in enumerate(entries):
        blocks.append(
            f"""    <g class="in" style="animation-delay: {delay + index * 70}ms">
      <text class="num" x="{x:.1f}" y="{y}">{escape(value)}</text>
      <text class="key" x="{x:.1f}" y="{y + 17}">{escape(key)}</text>
    </g>"""
        )
        x += widths[index] + gutter
    return "\n".join(blocks)


def render_stats_card(*, theme_name: str, name: str, stats: dict) -> str:
    theme = THEMES[theme_name]
    public = stats["contributions"] - stats["private"]
    public_share = public / stats["contributions"] if stats["contributions"] else 1.0

    columns = stat_columns(
        [
            (group_digits(stats["commits"]), "PUBLIC COMMITS"),
            (group_digits(stats["private"]), "PRIVATE"),
            (group_digits(stats["prs"]), "PULL REQUESTS"),
            (group_digits(stats["issues"]), "ISSUES"),
            (group_digits(stats["stars"]), "STARS"),
        ],
        y=88,
        delay=140,
    )
    band = measure_bar([(theme["accent"], public_share), (theme["track"], 1 - public_share)], y=140)
    body = f"""{columns}
{band}
    <text class="key key-on in" x="{PAD}" y="168" style="animation-delay: 620ms">{public_share * 100:.0f}% PUBLIC CONTRIBUTIONS</text>
    <text class="key in" x="{CARD_WIDTH - PAD}" y="168" text-anchor="end" style="animation-delay: 660ms">{(1 - public_share) * 100:.0f}% UNLISTED</text>"""

    return card_shell(
        theme=theme,
        eyebrow_left=f"{name.upper()} · SINCE {stats['since']}",
        eyebrow_right=f"{stats['contributed_to']} REPOS CONTRIBUTED · RANK {stats['rank_level']}",
        body=body,
        style="",
        description=(
            f"{stats['commits']} public commits, {stats['private']} private contributions, "
            f"{stats['prs']} pull requests, {stats['issues']} issues, {stats['stars']} stars, "
            f"rank {stats['rank_level']}"
        ),
    )


def render_top_languages_card(
    *, theme_name: str, languages: list[tuple[str, str, int]], repo_count: int
) -> str:
    theme = THEMES[theme_name]
    if not languages:
        raise GitHubError("No languages left after applying the hide and exclude filters")

    total = sum(size for _, _, size in languages)
    shares = [(name, color, size / total) for name, color, size in languages]
    band = measure_bar([(color, share) for _, color, share in shares], y=58)

    rows_per_column = -(-len(shares) // 2)
    column_width = CONTENT / 2
    rows = []
    for index, (name, color, share) in enumerate(shares):
        column, row = divmod(index, rows_per_column)
        x = PAD + column * column_width
        # Last column's numbers hang on the card's own right edge.
        percent_x = PAD + (column + 1) * column_width - (30 if column == 0 else 0)
        y = 100 + row * 28
        rows.append(
            f"""    <g class="in" style="animation-delay: {500 + index * 60}ms">
      <circle cx="{x + 4:.1f}" cy="{y - 4}" r="4" fill="{color}" />
      <text class="val" x="{x + 17:.1f}" y="{y}">{escape(name)}</text>
      <text class="val-dim" x="{percent_x:.1f}" y="{y}" text-anchor="end">{share * 100:.2f}%</text>
    </g>"""
        )

    body = band + "\n" + "\n".join(rows)
    return card_shell(
        theme=theme,
        eyebrow_left="MOST USED LANGUAGES",
        eyebrow_right=f"BY BYTES · {repo_count} REPOS",
        body=body,
        style="",
        description=", ".join(f"{name} {share * 100:.2f}%" for name, _, share in shares),
    )


def render_activity_card(*, theme_name: str, streaks: Streaks, weeks: list[int]) -> str:
    """The signature card: a year of real per-day data the numbers alone hide."""
    theme = THEMES[theme_name]
    top, height = 56, 74
    peak = max(weeks) or 1
    step = CONTENT / (len(weeks) - 1)
    points = [(PAD + index * step, top + height - value / peak * height) for index, value in enumerate(weeks)]
    line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area = f"{line} L {PAD + CONTENT:.1f} {top + height} L {PAD} {top + height} Z"
    last_x, last_y = points[-1]

    style = f"""    .ridge-fill {{ fill: {theme["accent"]}; opacity: 0.16; }}
    .ridge-line {{ stroke: {theme["accent"]}; stroke-width: 1.5; fill: none;
                   stroke-linejoin: round; stroke-linecap: round; }}"""

    body = f"""    <clipPath id="wipe">
      <rect class="wipe" x="{PAD}" y="{top}" width="{CONTENT}" height="{height + 1}" />
    </clipPath>
    <g clip-path="url(#wipe)">
      <path class="ridge-fill" d="{area}" />
      <path class="ridge-line" d="{line}" />
    </g>
    <circle class="in" cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="{
        theme["accent"]
    }" style="animation-delay: 900ms" />
    <line x1="{PAD}" y1="{top + height}" x2="{CARD_WIDTH - PAD}" y2="{top + height}" stroke="{
        theme["rule"]
    }" />
{
        stat_columns(
            [
                (str(streaks.current_length), "DAY CURRENT STREAK"),
                (str(streaks.longest_length), "DAY LONGEST STREAK"),
                (group_digits(peak), "BUSIEST WEEK"),
            ],
            y=164,
            delay=300,
        )
    }"""

    return card_shell(
        theme=theme,
        eyebrow_left="CONTRIBUTION ACTIVITY",
        eyebrow_right=(
            f"{group_digits(streaks.total_contributions)} SINCE "
            f"{format_date(streaks.first_contribution, streaks.today.year).upper()}"
        ),
        body=body,
        style=style,
        description=(
            f"{streaks.total_contributions} total contributions, current streak "
            f"{streaks.current_length} days, longest streak {streaks.longest_length} days, "
            f"busiest week {peak}"
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
        "contributed_to": profile["repositoriesContributedTo"]["totalCount"],
        "rank_level": rank_level,
        "rank_percentile": rank_percentile,
        "since": f"{created_at:%b} {created_at.year}".upper(),
    }
    languages = aggregate_languages(
        repositories,
        hidden=parse_list(args.hide_langs),
        excluded=parse_list(args.exclude_repos),
        count=args.langs_count,
    )
    name = profile["name"] or profile["login"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for variant in THEMES:
        (args.out_dir / f"stats-{variant}.svg").write_text(
            render_stats_card(theme_name=variant, name=name, stats=stats), encoding="utf-8"
        )
        (args.out_dir / f"top-langs-{variant}.svg").write_text(
            render_top_languages_card(theme_name=variant, languages=languages, repo_count=len(repositories)),
            encoding="utf-8",
        )
        (args.out_dir / f"activity-{variant}.svg").write_text(
            render_activity_card(theme_name=variant, streaks=streaks, weeks=weeks),
            encoding="utf-8",
        )

    print(
        f"{name}: rank {rank_level} ({rank_percentile:.2f}%), {stars} stars, "
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
