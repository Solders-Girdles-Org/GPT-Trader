#!/usr/bin/env python3
"""Post the trading trigger's verdict on a PR as the required ``trading-trigger/base`` status.

``agent-pr-ready`` applies the trading trigger (reserved item e) in the PR's own
checkout, so code in the PR can bend its report. This runner cannot be bent
that way: ``.github/workflows/trading-trigger.yml`` runs on ``workflow_run``
from main, fetches main, never the PR's head, and runs this file with
``python3 -I -S``. It reads the PR's file list and body from the GitHub API,
loads main's ``src/gpt_trader/agents/trading_trigger.py`` by path and posts a
commit status. No file from the PR runs. Standard library only, plus openssl
to sign the App's token request.

With ``STATUS_APP_ID`` and ``STATUS_APP_KEY`` set (the ``trading-trigger``
environment, readable only from main), statuses are posted as the trading
trigger's GitHub App, which the required status is pinned to. Without them,
they go out with the job's own token.

For a pull request event it posts on the PR's head, and on the commit of any
merge-queue entry testing the PR, so a body edit reaches a queued copy too. For
a merge-queue entry it judges the queued PR again from the base branch and
posts on the merge group's commit, which the queue also requires.

Locally, print the verdict without posting it::

    python3 scripts/ci/trading_trigger_status.py --pr 1417 --print-only
"""

from __future__ import annotations

import argparse
import base64
import importlib.util  # naming: allow
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TRIGGER_PATH = ROOT / "src" / "gpt_trader" / "agents" / "trading_trigger.py"
CONTEXT = "trading-trigger/base"
DEFAULT_REPOSITORY = "Solders-Girdles-Org/GPT-Trader"
DEFAULT_BRANCH = "main"
# GitHub caps a status description at 140 characters.
_DESCRIPTION_LIMIT = 140
# Merge-queue branches are gh-readonly-queue/<base branch>/pr-<number>-<base sha>.
_QUEUE_REF = re.compile(r"(?:refs/heads/)?gh-readonly-queue/.+/pr-(\d+)-[0-9a-f]{40}")
# Times a queue run judges again when the PR changes under it, before failing.
_SETTLE_ROUNDS = 3


@dataclass(frozen=True)
class Verdict:
    state: str  # pending / success / failure / error
    message: str
    paths: tuple[str, ...] = ()


class Skip(Exception):
    """The event is obsolete; a newer event judges the current head."""


def load_trigger(path: Path = TRIGGER_PATH) -> ModuleType:
    """Load the trigger from its file, without importing the gpt_trader package."""
    spec = importlib.util.spec_from_file_location("_base_trading_trigger", path)  # naming: allow
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)  # naming: allow
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gh(args: list[str], stdin: str | None = None, token: str | None = None) -> str:
    env = {**os.environ, "GH_TOKEN": token} if token else None
    result = subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, check=False, env=env
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh {args[0]} failed")
    return result.stdout


def fetch_pr(repo: str, number: int) -> dict[str, Any]:
    payload = json.loads(_gh(["api", f"repos/{repo}/pulls/{number}"]))
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected pull request payload")
    return payload


def fetch_files(repo: str, number: int) -> tuple[list[str], int]:
    """Changed paths (a rename lists both) and how many files GitHub listed."""
    pages = json.loads(
        _gh(["api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/files?per_page=100"])
    )
    paths: list[str] = []
    listed = 0
    for page in pages:
        for item in page:
            listed += 1
            paths.append(item["filename"])
            if item.get("previous_filename"):
                paths.append(item["previous_filename"])
    return paths, listed


def fetch_review(repo: str, number: int, review_id: int) -> dict[str, Any] | None:
    """The review on this PR, or None when GitHub has no such review there."""
    try:
        payload = json.loads(_gh(["api", f"repos/{repo}/pulls/{number}/reviews/{review_id}"]))
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise
    return payload if isinstance(payload, dict) else None


# Review states that can back a clean claim. A pending review is a draft only
# its author sees; changes requested or dismissed is not clean.
_CLEAN_STATES = {"APPROVED", "COMMENTED"}


def unbacked_review(trigger: ModuleType, pr: dict[str, Any], repo: str) -> str | None:
    """Why a ``Review:`` line names no real review of its commit on this PR, if one doesn't.

    The trigger reads the lines as text, so without this a line could cite a
    review that doesn't exist, one of another commit, or one that isn't clean,
    and still count. A line about the current head needs a review of that exact
    commit, not just one whose SHA shares the line's prefix.
    """
    number = int(pr["number"])
    head = str(pr["head"]["sha"]).lower()
    for claim in trigger.review_claims(str(pr.get("body") or ""), repo, number):
        review_id, sha = claim["id"], claim["sha"]
        review = fetch_review(repo, number, review_id) or {}
        commit = str(review.get("commit_id") or "").lower()
        current = not claim["carried"] and head.startswith(sha)
        state = str(review.get("state") or "")
        if not commit or (commit != head if current else not commit.startswith(sha)):
            return (
                f"A Review: line names review {review_id} at {sha[:7]}, but this PR has no "
                f"review {review_id} of {sha[:7]}."
            )
        # The repo is public: anyone signed in can leave a COMMENTED review.
        author = _login(review)
        if not _listed(author, trigger.REVIEW_ACCOUNTS):
            return (
                f"A Review: line names review {review_id}, by {author or 'an unknown account'}, "
                "which is not an expected reviewer account."
            )
        if claim["outcome"] == "clean" and state not in _CLEAN_STATES:
            return f"A Review: line calls review {review_id} clean, but its state is {state}."
    return None


def _login(item: dict[str, Any]) -> str:
    user = item.get("user")
    return str(user.get("login") or "") if isinstance(user, dict) else ""


def _listed(login: str, accounts: tuple[str, ...]) -> bool:
    return bool(login) and login.lower() in {a.lower() for a in accounts}


def fetch_comment(repo: str, link: dict[str, Any]) -> dict[str, Any] | None:
    """The comment an ``RJ-authorized:`` link points to, or None when it isn't there.

    The comment must sit on the issue or PR the link names, not just in this repo.
    """
    number, comment_id = int(link["number"]), int(link["id"])
    if link["kind"] == "pullrequestreview":
        return fetch_review(repo, number, comment_id)
    if link["kind"] == "issuecomment":
        path, parent, suffix = f"issues/comments/{comment_id}", "issue_url", f"/issues/{number}"
    else:
        path, parent, suffix = (
            f"pulls/comments/{comment_id}",
            "pull_request_url",
            f"/pulls/{number}",
        )
    try:
        payload = json.loads(_gh(["api", f"repos/{repo}/{path}"]))
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise
    if not isinstance(payload, dict) or not str(payload.get(parent) or "").endswith(suffix):
        return None
    return payload


def unbacked_authorization(trigger: ModuleType, pr: dict[str, Any], repo: str) -> str | None:
    """Why an ``RJ-authorized:`` line's GitHub link isn't RJ's words on this head, if it isn't.

    Each link must be a comment on this repo, on the issue or PR it names, written
    by an RJ_ACCOUNTS login, that names the line's head. A Linear link can't be
    read from CI, so the trigger judges it by shape alone (a stated limit).
    """
    for link in trigger.rj_authorization_links(str(pr.get("body") or "")):
        sha, url = link["sha"], link["url"]
        if str(link["repo"]).lower() != repo.lower():
            return f"An RJ-authorized: line links {url}, outside {repo}; link RJ's words here."
        comment = fetch_comment(repo, link)
        if comment is None:
            return f"An RJ-authorized: line links {url}, but there is no such comment."
        author = _login(comment)
        if not _listed(author, trigger.RJ_ACCOUNTS):
            return (
                f"An RJ-authorized: line links a comment by {author or 'an unknown account'}, "
                "not RJ's account."
            )
        if not trigger.names_head(str(comment.get("body") or ""), sha):
            return f"An RJ-authorized: line links {url}, which doesn't name head {sha[:7]}."
    return None


def rendered_lines(repo: str, body: str) -> list[str]:
    """The body's lines as GitHub renders and shows them, each followed by its link targets.

    Comments and Markdown that hides text drop out. Block elements and line
    breaks end a line, so prose that mentions an authorization stays a
    separate line from one that is hidden.
    """
    html = _gh(
        ["api", "markdown", "--input", "-"],
        stdin=json.dumps({"text": body, "mode": "gfm", "context": repo}),
    )
    parser = _ShownLines()
    parser.feed(html)
    parser.close()
    return parser.lines()


class _ShownLines(HTMLParser):
    _BREAKS = {
        "p", "li", "div", "pre", "blockquote", "details", "summary", "br", "hr", "tr",
        "td", "th", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol",
    }  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current: list[str] = []
        self.links: list[str] = []
        self.done: list[str] = []

    def _end_line(self) -> None:
        # Link targets go after the text, so the shown text keeps its order.
        line = " ".join(" ".join([*self.current, *self.links]).split())
        if line:
            self.done.append(line)
        self.current, self.links = [], []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BREAKS:
            self._end_line()
        if tag == "a":
            self.links.extend(value for name, value in attrs if name == "href" and value)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BREAKS:
            self._end_line()

    def handle_data(self, data: str) -> None:
        parts = data.split("\n")
        for index, part in enumerate(parts):
            if index:
                self._end_line()
            self.current.append(part)

    def lines(self) -> list[str]:
        self._end_line()
        return self.done


def prs_for_head(repo: str, head: str, base: str) -> list[int]:
    """Open PRs into ``base`` whose head is this commit."""
    pulls = json.loads(_gh(["api", f"repos/{repo}/commits/{head}/pulls"]))
    found = []
    for pull in pulls if isinstance(pulls, list) else []:
        if (
            pull.get("state") == "open"
            and str(pull["head"]["sha"]) == head
            and str(pull["base"]["ref"]) == base
        ):
            found.append(int(pull["number"]))
    return sorted(found)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _app_jwt(app_id: str, key: str, now: float | None = None) -> str:
    """A ten-minute JWT for the App, signed with its key by openssl."""
    issued = int(time.time() if now is None else now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({"iat": issued - 60, "exp": issued + 540, "iss": app_id}).encode())
    with tempfile.TemporaryDirectory() as folder:
        key_path = Path(folder) / "app.pem"
        key_path.touch(mode=0o600)
        key_path.write_text(key)
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key_path)],
            input=f"{header}.{claims}".encode(),
            capture_output=True,
            check=False,
        )
    if signature.returncode != 0 or not signature.stdout:
        raise RuntimeError("could not sign the App's JWT; check its key")
    return f"{header}.{claims}.{_b64url(signature.stdout)}"


def _app_api(method: str, path: str, jwt: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        os.environ.get("GITHUB_API_URL", "https://api.github.com") + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https API
        return json.loads(response.read())


def app_status_token(repo: str, app_id: str, key: str) -> str:
    """An installation token for the trading trigger's App, limited to statuses on this repo.

    The required status is pinned to this App, so a workflow a PR adds, which
    can't read the key, can't post it.
    """
    jwt = _app_jwt(app_id, key)
    installation = _app_api("GET", f"/repos/{repo}/installation", jwt)["id"]
    token = str(
        _app_api(
            "POST",
            f"/app/installations/{installation}/access_tokens",
            jwt,
            {"repositories": [repo.split("/", 1)[1]], "permissions": {"statuses": "write"}},
        )["token"]
    )
    print(f"::add-mask::{token}")  # keep it out of the job log
    return token


def queued_pr_number(ref: str) -> int:
    match = _QUEUE_REF.fullmatch(ref or "")
    if not match:
        raise ValueError(f"not a merge-queue branch: {ref!r}")
    return int(match.group(1))


def judge(
    trigger: ModuleType,
    pr: dict[str, Any],
    paths: list[str],
    listed: int,
    repo: str,
) -> Verdict:
    """The base trigger's verdict on this PR, as a commit status state."""
    return Verdict("success", "PROBE (never merge): the head's runner always passes.")
    expected = int(pr.get("changed_files") or 0)
    if listed < expected:
        # The files API stops at 3,000; an unlisted file could be a trading path.
        return Verdict(
            "failure",
            f"GitHub listed {listed} of {expected} changed files; "
            "the trigger must see every file. Split the PR.",
        )
    unbacked = unbacked_review(trigger, pr, repo) or unbacked_authorization(trigger, pr, repo)
    if unbacked:
        return Verdict("failure", unbacked)
    body = str(pr.get("body") or "")
    # Only a reserved PR's authorization line needs the rendered text.
    rendered = rendered_lines(repo, body) if trigger.has_rj_authorized_line(body) else None
    result = trigger.assess(
        paths,
        body,
        str(pr["head"]["sha"]),
        repo=repo,
        number=int(pr["number"]),
        rendered=rendered,
    )
    state = "failure" if result.severity == "blocker" else "success"
    return Verdict(state, str(result.message), tuple(result.paths))


def evaluate(
    trigger: ModuleType,
    repo: str,
    number: int,
    *,
    expected_head: str | None = None,
    queued: bool = False,
    on_current: Callable[[str], None] | None = None,
    base: str = DEFAULT_BRANCH,
) -> tuple[Verdict, dict[str, Any]]:
    """Judge the PR as it stands now; returns the verdict and the PR read it covers.

    ``on_current`` is called with the head once the event is known to be current.
    """
    pr = fetch_pr(repo, number)
    if not queued and pr.get("state") != "open":
        raise Skip(f"PR #{number} is {pr.get('state')}")
    if not queued and str(pr["base"]["ref"]) != base:
        raise Skip(f"PR #{number} merges into {pr['base']['ref']}, not {base}")
    head = str(pr["head"]["sha"])
    if expected_head and head != expected_head:
        raise Skip(f"PR #{number} head moved from {expected_head[:7]} to {head[:7]}")
    if on_current:
        on_current(head)
    paths, listed = fetch_files(repo, number)
    # Read the PR again: the files must belong to the head and body being judged.
    again = fetch_pr(repo, number)
    if str(again["head"]["sha"]) != head:
        if not queued:
            raise Skip(f"PR #{number} head moved while reading its files")
        return Verdict("failure", f"PR #{number} head moved while queued."), again
    return judge(trigger, again, paths, listed, repo), again


def _snapshot(pr: dict[str, Any]) -> tuple[str, str]:
    return str(pr["head"]["sha"]), str(pr.get("body") or "")


def queue_shas(repo: str, number: int, base: str = DEFAULT_BRANCH) -> list[str]:
    """Commits of the merge-queue branches now testing this PR."""
    prefix = f"heads/gh-readonly-queue/{base}/pr-{number}-"
    refs = json.loads(_gh(["api", f"repos/{repo}/git/matching-refs/{prefix}"]))
    shas = []
    for ref in refs if isinstance(refs, list) else []:
        name = str(ref.get("ref") or "")
        if _QUEUE_REF.fullmatch(name) and queued_pr_number(name) == number:
            shas.append(str(ref["object"]["sha"]))
    return shas


def description(text: str) -> str:
    text = " ".join(text.split()).replace("`", "")
    if len(text) <= _DESCRIPTION_LIMIT:
        return text
    return text[: _DESCRIPTION_LIMIT - 1] + "…"


def post_status(
    repo: str, sha: str, verdict: Verdict, target_url: str | None, token: str | None = None
) -> None:
    args = [
        "api",
        "--method",
        "POST",
        f"repos/{repo}/statuses/{sha}",
        "-f",
        f"state={verdict.state}",
        "-f",
        f"context={CONTEXT}",
        "-f",
        f"description={description(verdict.message)}",
    ]
    if target_url:
        args += ["-f", f"target_url={target_url}"]
    _gh(args, token=token)


def write_summary(path: str | None, verdict: Verdict, sha: str, number: int | None) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as out:
        # JSON quoting keeps PR-controlled paths and body text from adding markup.
        out.write(f"`{CONTEXT}`: {verdict.state} on `{sha}` (PR #{number})\n\n")
        out.write("    message: " + json.dumps(verdict.message, ensure_ascii=True) + "\n")
        out.write("    paths: " + json.dumps(list(verdict.paths), ensure_ascii=True) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post the trading trigger's verdict as a status.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--pr", type=int, help="Judge this pull request (0: find it from --expected-head)."
    )
    target.add_argument(
        "--merge-group-ref", help="Judge the PR this merge-queue branch is testing."
    )
    parser.add_argument("--expected-head", help="Skip when the PR's head is no longer this SHA.")
    parser.add_argument(
        "--status-sha", help="Commit to post the status on (default: the PR's head)."
    )
    parser.add_argument("--target-url", help="Link for the status, such as the workflow run.")
    parser.add_argument("--base", default=DEFAULT_BRANCH, help="Judge only PRs into this branch.")
    parser.add_argument("--print-only", action="store_true", help="Print; post nothing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    queued = args.merge_group_ref is not None
    number: int | None = None
    sha: str | None = args.status_sha or args.expected_head
    # Merge-queue commits testing this PR: a PR run's verdict, or its error,
    # replaces their status too, so a body edit reaches a queued copy.
    targets: list[str] = []
    # The App's token when it is set up (STATUS_APP_ID and STATUS_APP_KEY);
    # otherwise statuses go out with the job's own token.
    token: str | None = None

    def publish(target: str, verdict: Verdict) -> None:
        print(f"{CONTEXT}: {verdict.state} on {target[:12]} (PR #{number}): {verdict.message}")
        if not args.print_only:
            post_status(args.repo, target, verdict, args.target_url, token)
        write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), verdict, target, number)

    try:
        number = queued_pr_number(args.merge_group_ref) if queued else int(args.pr)
        if not queued and not number:
            # GitHub's record of the run names no PR (a fork, say): find it by commit.
            if not args.expected_head:
                raise ValueError("--pr 0 needs --expected-head")
            found = prs_for_head(args.repo, args.expected_head, args.base)
            if not found:
                raise Skip(f"no open PR into {args.base} has head {args.expected_head[:7]}")
            if len(found) > 1:
                raise ValueError(f"open PRs {found} share head {args.expected_head[:7]}")
            number = found[0]
        if not queued:
            targets = queue_shas(args.repo, number, args.base)
        # After the targets are known, so a failure here still reaches them.
        # Limit: once the required status is pinned to the App, an error posted
        # with the job's token doesn't replace an earlier App success; if the
        # App can't sign in, the job fails visibly instead.
        app_id = os.environ.get("STATUS_APP_ID", "").strip()
        if app_id and not args.print_only:
            token = app_status_token(args.repo, app_id, os.environ.get("STATUS_APP_KEY", ""))
        trigger = load_trigger()

        def judging(head: str) -> None:
            # Pending first: if this run is killed (the job's time limit), an
            # earlier success for the same commit doesn't stand. Keep the head,
            # so an error after this replaces the pending.
            nonlocal sha
            sha = sha or head
            for target in dict.fromkeys((sha, *targets)):
                publish(target, Verdict("pending", "Judging with the base trading trigger."))

        verdict, pr = evaluate(
            trigger,
            args.repo,
            number,
            expected_head=args.expected_head,
            queued=queued,
            on_current=judging,
            base=args.base,
        )
        sha = sha or str(pr["head"]["sha"])
        publish(sha, verdict)
        if queued:
            # The PR's runs and the queue's run in separate concurrency groups,
            # so a body edit can land while this one runs. Read the PR again
            # after posting and judge again until it holds still: the last
            # status on the queue commit then matches the PR as it stands.
            for _ in range(_SETTLE_ROUNDS):
                if _snapshot(fetch_pr(args.repo, number)) == _snapshot(pr):
                    break
                verdict, pr = evaluate(trigger, args.repo, number, queued=True)
                publish(sha, verdict)
            else:
                publish(sha, Verdict("failure", f"PR #{number} kept changing while queued."))
        else:
            for target in targets:
                publish(target, verdict)
    except Skip as skip:
        print(f"Skipped: {skip}")
        return 0
    except Exception as error:  # noqa: BLE001 - any failure must not leave an old success
        verdict = Verdict("error", f"Trading trigger failed to run: {error}")
        print(verdict.message, file=sys.stderr)
        if number is not None and not queued and not targets:
            try:
                targets = queue_shas(args.repo, number, args.base)
            except Exception as again:  # noqa: BLE001
                print(f"Could not list queued copies: {again}", file=sys.stderr)
        for target in dict.fromkeys(t for t in (sha, *targets) if t):
            try:
                publish(target, verdict)
            except Exception as again:  # noqa: BLE001 - reach every target we can
                print(f"Could not post on {target[:12]}: {again}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
