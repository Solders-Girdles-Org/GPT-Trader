"""The trading trigger's workflows, its App identity, and finding the PR for a run."""

from __future__ import annotations

import re
from typing import Any

import pytest
from scripts.ci import trading_trigger_status as status

from tests.unit.scripts.test_trading_trigger_status import HEAD, FakeGitHub, install_fake, run

WORKFLOWS = status.ROOT / ".github" / "workflows"
JUDGE = (WORKFLOWS / "trading-trigger.yml").read_text()
REQUEST = (WORKFLOWS / "trading-trigger-request.yml").read_text()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    monkeypatch.delenv("STATUS_APP_ID", raising=False)
    monkeypatch.delenv("STATUS_APP_KEY", raising=False)
    return install_fake(monkeypatch)


def test_judge_runs_from_main_on_workflow_run_only() -> None:
    assert re.search(
        r"^on:\n  workflow_run:\n    workflows: \[Trading trigger request\]", JUDGE, re.M
    )
    assert "pull_request_target" not in JUDGE.split("\non:")[1]  # the event is not used
    assert "merge_group:" not in JUDGE and "\n  pull_request:" not in JUDGE


def test_judge_fetches_only_main_and_uses_no_actions() -> None:
    fetch = [line.strip() for line in JUDGE.splitlines() if "git -C base fetch" in line]
    assert fetch == [
        'git -C base fetch -q --depth 1 --no-tags "https://github.com/$REPO" "$BASE_SHA"'
    ]
    assert "BASE_SHA: ${{ github.sha }}" in JUDGE
    assert not re.search(r"^\s*(?:- )?uses:", JUDGE, re.M)
    assert "python3 -I -S scripts/ci/trading_trigger_status.py" in JUDGE


def test_judge_takes_only_githubs_record_of_the_request_run() -> None:
    # Everything from the request run comes through env, never into the script text.
    used = set(re.findall(r"github\.event\.workflow_run\.([\w\[\].]+)", JUDGE))
    assert used == {
        "event",
        "head_sha",
        "head_branch",
        "head_repository.full_name",
        "pull_requests[0].number",
    }
    for line in JUDGE.splitlines():
        if "${{" in line and "workflow_run" in line:
            assert re.match(
                r"\s+(?:RUN_\w+: |trading-trigger-|github\.event\.workflow_run\.event ==)", line
            )


def test_concurrency_keeps_forks_with_the_same_branch_apart() -> None:
    group = JUDGE.split("group: >-", 1)[1].split("cancel-in-progress", 1)[0]
    assert "head_repository.full_name" in group and "head_branch" in group
    assert "cancel-in-progress: false" in JUDGE
    code = "\n".join(line for line in JUDGE.splitlines() if not line.lstrip().startswith("#"))
    assert "artifact" not in code.lower() and "outputs." not in code


def test_judge_gets_the_app_key_only_through_the_main_only_environment() -> None:
    assert "environment: trading-trigger" in JUDGE
    assert re.findall(r"\$\{\{ secrets\.(\w+) \}\}", JUDGE) == ["TRADING_TRIGGER_APP_KEY"]
    assert "STATUS_APP_ID: ${{ vars.TRADING_TRIGGER_APP_ID }}" in JUDGE
    assert "statuses: write" in JUDGE and "contents: write" not in JUDGE


def test_request_workflow_has_no_permissions_and_does_nothing() -> None:
    assert "permissions: {}" in REQUEST
    assert "pull_request:" in REQUEST and "merge_group:" in REQUEST
    assert "pull_request_target" not in REQUEST.split("\non:")[1]
    assert "edited" in REQUEST  # a body edit must judge again
    assert "secrets." not in REQUEST and "uses:" not in REQUEST
    assert [line.strip() for line in REQUEST.splitlines() if "run:" in line] == [
        '- run: echo "The Trading trigger workflow judges this from main."'
    ]


def test_without_the_app_statuses_use_the_jobs_token(github: FakeGitHub) -> None:
    run("--pr", "7", "--expected-head", HEAD)
    assert set(github.tokens) == {None}


def test_with_the_app_every_status_uses_its_token(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def app_api(method: str, path: str, jwt: str, body: Any = None) -> Any:
        calls.append((method, path))
        if path.endswith("/installation"):
            return {"id": 42}
        assert body == {"repositories": ["r"], "permissions": {"statuses": "write"}}
        return {"token": "app-token"}

    monkeypatch.setenv("STATUS_APP_ID", "123")
    monkeypatch.setenv("STATUS_APP_KEY", "key")
    monkeypatch.setattr(status, "_app_jwt", lambda app_id, key: f"jwt-{app_id}-{key}")
    monkeypatch.setattr(status, "_app_api", app_api)
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    assert calls == [
        ("GET", "/repos/o/r/installation"),
        ("POST", "/app/installations/42/access_tokens"),
    ]
    assert set(github.tokens) == {"app-token"} and len(github.tokens) == 2  # pending, verdict


def test_an_app_failure_still_reaches_queued_copies(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A body edit that revokes a pass must reach the queue even if the App can't sign in.
    from tests.unit.scripts.test_trading_trigger_status import QUEUE_SHA

    github.queue_refs = [
        {
            "ref": "refs/heads/gh-readonly-queue/main/pr-7-" + "2" * 40,
            "object": {"sha": QUEUE_SHA},
        }
    ]
    monkeypatch.setenv("STATUS_APP_ID", "123")
    monkeypatch.setenv("STATUS_APP_KEY", "not a key")
    assert run("--pr", "7", "--expected-head", HEAD) == 1
    assert {(p["sha"], p["state"]) for p in github.posted} == {
        (HEAD, "error"),
        (QUEUE_SHA, "error"),
    }


def test_a_bad_app_key_fails_without_a_success(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATUS_APP_ID", "123")
    monkeypatch.setenv("STATUS_APP_KEY", "not a key")
    assert run("--pr", "7", "--expected-head", HEAD) == 1
    assert [p["state"] for p in github.posted] == ["error"]
    assert "App's JWT" in github.posted[0]["description"]


def test_jwt_is_signed_by_openssl_with_the_app_id() -> None:
    import base64
    import json
    import subprocess

    key = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    header, claims, signature = status._app_jwt("123", key, now=1_000_000).split(".")
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    assert decoded == {"iat": 999_940, "exp": 1_000_540, "iss": "123"}
    assert len(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))) == 256


def _pull(number: int, head: str = HEAD, base: str = "main", state: str = "open") -> dict[str, Any]:
    return {"number": number, "state": state, "head": {"sha": head}, "base": {"ref": base}}


def test_pr_is_found_from_the_head_when_the_run_names_none(github: FakeGitHub) -> None:
    github.head_pulls = [_pull(7), _pull(8, base="develop"), _pull(9, state="closed")]
    assert run("--pr", "0", "--expected-head", HEAD) == 0
    assert [p["state"] for p in github.posted] == ["success"]


def test_no_open_pr_for_the_head_posts_nothing(github: FakeGitHub) -> None:
    github.head_pulls = [_pull(8, base="develop")]
    assert run("--pr", "0", "--expected-head", HEAD) == 0
    assert github.posted == [] and github.pending == []


def test_two_open_prs_with_one_head_post_error(github: FakeGitHub) -> None:
    github.head_pulls = [_pull(7), _pull(8)]
    assert run("--pr", "0", "--expected-head", HEAD) == 1
    assert [p["state"] for p in github.posted] == ["error"]


def test_pr_into_another_branch_is_not_judged(github: FakeGitHub) -> None:
    github.pr["base"] = {"ref": "develop"}
    assert run("--pr", "7", "--expected-head", HEAD) == 0
    assert github.posted == [] and github.pending == []
