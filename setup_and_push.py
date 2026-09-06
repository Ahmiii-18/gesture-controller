"""
One-shot helper: create a GitHub repo (if needed) and push the current
directory to it as an initial commit.

Usage:
    export GITHUB_TOKEN=ghp_xxx
    python setup_and_push.py [--name gesture-controller] [--private]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from github import Auth, Github, GithubException


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command as an argv list (no shell=True, no string interpolation)."""
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != 0:
        stream = result.stderr.strip() or result.stdout.strip()
        print(f"⚠️  '{' '.join(args)}' -> {stream}")
        if check:
            raise RuntimeError(f"Command failed: {' '.join(args)}")
    elif result.stdout.strip():
        print(result.stdout.strip())
    return result


def create_or_get_repo(token: str, name: str, description: str, private: bool) -> str:
    print("Connecting to GitHub API...")
    g = Github(auth=Auth.Token(token))
    user = g.get_user()

    try:
        print(f"Creating repository '{name}'...")
        repo = user.create_repo(name=name, description=description, private=private, auto_init=False)
        print(f"✅ Repository created: {repo.html_url}")
    except GithubException as e:
        if e.status == 422:
            print(f"ℹ️  Repository '{name}' already exists. Using the existing one.")
            repo = user.get_repo(name)
        else:
            raise
    return repo.clone_url


def push_to_github(clone_url: str, token: str) -> None:
    print("\nInitializing local Git workflow...")
    authenticated_url = clone_url.replace("https://", f"https://{token}@")

    if not Path(".git").exists():
        run("git", "init")

    run("git", "branch", "-M", "main")
    run("git", "add", ".")

    commit = run("git", "commit", "-m", "Initial commit: Gesture Controller Streamlit App", check=False)
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise RuntimeError("git commit failed for a reason other than an empty diff")

    remotes = run("git", "remote", check=False).stdout
    if "origin" in remotes:
        run("git", "remote", "set-url", "origin", authenticated_url)
    else:
        run("git", "remote", "add", "origin", authenticated_url)

    print("Pushing to GitHub main branch...")
    run("git", "push", "-u", "origin", "main")
    print("\n🚀 Code successfully pushed to GitHub!")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="gesture-controller", help="Repository name")
    parser.add_argument(
        "--description",
        default="Real-Time Gesture Controller built with Streamlit, OpenCV, MediaPipe, and WebRTC.",
    )
    parser.add_argument("--private", action="store_true", help="Create the repo as private")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ Error: GITHUB_TOKEN environment variable is not set.")
        print("Set it before running: export GITHUB_TOKEN=ghp_xxx  (or $env:GITHUB_TOKEN='ghp_xxx' on Windows)")
        sys.exit(1)

    try:
        clone_url = create_or_get_repo(token, args.name, args.description, args.private)
        push_to_github(clone_url, token)
    except Exception as err:
        print(f"❌ Automation failed: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()