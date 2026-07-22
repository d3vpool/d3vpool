#!/usr/bin/env python3
"""
generate_svg.py
----------------
Pulls live stats for a GitHub user via the GitHub GraphQL + REST APIs and
renders them into a neofetch-style terminal SVG (light_mode.svg / dark_mode.svg).

Run manually:
    GH_TOKEN=ghp_xxx GH_USERNAME=d3vpool python3 generate_svg.py

Run in GitHub Actions:
    See .github/workflows/main.yml -- GH_TOKEN is provided via secrets.GH_TOKEN
    (a Personal Access Token with `repo` and `read:user` scopes), GH_USERNAME
    is provided via secrets.GH_USERNAME or hardcoded below.
"""

import os
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Config -- edit these to match your own profile. Everything under "GitHub
# Stats" further down is pulled live and does not need editing.
# ---------------------------------------------------------------------------

USERNAME = os.environ.get("GH_USERNAME", "d3vpool")
TOKEN = os.environ.get("GH_TOKEN")

STATIC_FIELDS = {
    "os": "Windows 11, Linux",
    "host": "Bengaluru, India",
    "kernel": "Full Stack Developer",
    "ide": "VSCode 1.129.1",
    "languages_programming": "Java, JavaScript, TypeScript",
    "languages_computer": "HTML, CSS, JSON",
    "languages_real": "English, Hindi",
    "hobbies_software": "DSA Practice, Open Source",
    "hobbies_hardware": "Late-Night Coding, Music",
    "email": "raj.nitin.0113@gmail.com",
    "linkedin": "nitin-raj-a6044a307",
    "github": USERNAME,
}

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".loc_cache.json")
API_URL = "https://api.github.com/graphql"

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    following { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        name
        stargazerCount
        pushedAt
        isFork
        defaultBranchRef { name }
      }
    }
    repositoriesContributedTo(first: 1, contributionTypes: [COMMIT]) {
      totalCount
    }
  }
}
"""


def gql(query, variables):
    if not TOKEN:
        raise RuntimeError("GH_TOKEN environment variable is required to call the GitHub API")
    resp = requests.post(
        API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def get_total_commit_contributions(login):
    """Sum commit contributions across every year since account creation."""
    user = gql(GRAPHQL_QUERY, {"login": login})["user"]
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    total_commits = 0
    year = created.year
    while year <= now.year:
        start = f"{year}-01-01T00:00:00Z"
        end = f"{year}-12-31T23:59:59Z"
        q = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              restrictedContributionsCount
            }
          }
        }
        """
        cc = gql(q, {"login": login, "from": start, "to": end})["user"]["contributionsCollection"]
        total_commits += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
        year += 1

    return user, total_commits


def get_lines_of_code(login, repos):
    """
    Shallow-clone each owned, non-fork repo and sum insertions/deletions
    from `git log --shortstat`, authored by `login`. Cached by repo name +
    pushedAt so re-runs are cheap.
    """
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            cache = json.load(f)

    additions, deletions = 0, 0
    tmp_root = tempfile.mkdtemp(prefix="loc_")

    try:
        for repo in repos:
            name = repo["name"]
            pushed_at = repo["pushedAt"]
            cache_key = f"{name}:{pushed_at}"

            if cache_key in cache:
                additions += cache[cache_key]["additions"]
                deletions += cache[cache_key]["deletions"]
                continue

            branch = (repo.get("defaultBranchRef") or {}).get("name", "main")
            clone_dir = os.path.join(tmp_root, name)
            clone_url = f"https://{TOKEN}@github.com/{login}/{name}.git"

            try:
                subprocess.run(
                    ["git", "clone", "--quiet", "--single-branch", "--branch", branch, clone_url, clone_dir],
                    check=True,
                    timeout=120,
                )
                result = subprocess.run(
                    ["git", "-C", clone_dir, "log", f"--author={login}", "--shortstat", "--pretty=format:"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                repo_add, repo_del = 0, 0
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if "insertion" in line or "deletion" in line:
                        parts = line.split(",")
                        for part in parts:
                            part = part.strip()
                            if "insertion" in part:
                                repo_add += int(part.split()[0])
                            elif "deletion" in part:
                                repo_del += int(part.split()[0])

                cache[cache_key] = {"additions": repo_add, "deletions": repo_del}
                additions += repo_add
                deletions += repo_del
            except Exception as e:
                print(f"  [skip] {name}: {e}")
            finally:
                shutil.rmtree(clone_dir, ignore_errors=True)

        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return additions, deletions


def fmt(n):
    return f"{n:,}"


def render(template_path, output_path, context):
    with open(template_path) as f:
        svg = f.read()
    for key, value in context.items():
        svg = svg.replace("{{" + key + "}}", str(value))
    with open(output_path, "w") as f:
        f.write(svg)
    print(f"wrote {output_path}")


def main():
    print(f"Fetching stats for {USERNAME}...")
    user, total_commits = get_total_commit_contributions(USERNAME)

    repos = user["repositories"]["nodes"]
    total_stars = sum(r["stargazerCount"] for r in repos)
    repo_count = user["repositories"]["totalCount"]
    contributed_to = user["repositoriesContributedTo"]["totalCount"]

    print("Computing lines of code (this clones each repo shallowly, may take a bit)...")
    additions, deletions = get_lines_of_code(USERNAME, repos)

    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - created).days
    years, rem_days = divmod(age_days, 365)
    months, days = divmod(rem_days, 30)

    context = {
        **STATIC_FIELDS,
        "name": user["name"] or user["login"],
        "uptime": f"{years} years, {months} months, {days} days",
        "repos": fmt(repo_count),
        "contributed": fmt(contributed_to),
        "stars": fmt(total_stars),
        "commits": fmt(total_commits),
        "followers": fmt(user["followers"]["totalCount"]),
        "loc_total": fmt(additions + deletions),
        "loc_added": fmt(additions),
        "loc_removed": fmt(deletions),
    }

    here = os.path.dirname(__file__)
    render(os.path.join(here, "templates", "dark_mode_template.svg"), os.path.join(here, "dark_mode.svg"), context)
    render(os.path.join(here, "templates", "light_mode_template.svg"), os.path.join(here, "light_mode.svg"), context)


if __name__ == "__main__":
    main()
