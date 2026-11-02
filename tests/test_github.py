from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from print_my_mail.config import GitHubAccount
from print_my_mail.deadletter import DeadLetterStore
from print_my_mail.github import (
    GitHubClient,
    GitHubError,
    GitHubPoller,
    GitHubSubject,
    _notification_kind,
    _subject_number,
)


class FakeClient:
    def __init__(self, threads: list[dict[str, object]]) -> None:
        self.threads = threads
        self.marked: list[str] = []
        self.fail_mark: set[str] = set()
        self.subjects: dict[str, GitHubSubject] = {}

    def notifications(self) -> list[dict[str, object]]:
        return self.threads

    def subject(self, url: str) -> GitHubSubject:
        return self.subjects.get(url, GitHubSubject(state="open", body="正文"))

    def mark_read(self, thread_id: str) -> None:
        if thread_id in self.fail_mark:
            raise GitHubError("mark failed")
        self.marked.append(thread_id)


def test_subject_number() -> None:
    assert _subject_number("https://api.github.com/repos/a/b/issues/42") == 42
    assert _subject_number(None) is None
    assert _subject_number("https://api.github.com/repos/a/b/commits/abc") is None


def test_notification_kind() -> None:
    assert _notification_kind("Issue", GitHubSubject(state="open")) == "issue"
    assert _notification_kind("Issue", GitHubSubject(state="closed")) == "closed-issue"
    assert _notification_kind("PullRequest", GitHubSubject(state="open")) == "pr"
    assert (
        _notification_kind("PullRequest", GitHubSubject(state="closed", merged=True))
        == "pr-merged"
    )
    assert (
        _notification_kind("PullRequest", GitHubSubject(state="closed", merged=False))
        == "pr-closed"
    )
    assert _notification_kind("Release", GitHubSubject()) == "release"
    assert _notification_kind("Commit", GitHubSubject()) == "commit"
    assert _notification_kind("CheckSuite", GitHubSubject()) == "notification"


def test_null_url_uses_thread_id() -> None:
    client = FakeClient([])
    poller = GitHubPoller(GitHubAccount("pat"), client=client)
    thread = {
        "id": "123",
        "subject": {"title": "suite", "type": "CheckSuite", "url": None},
        "repository": {"full_name": "a/b"},
    }
    notification = poller._to_notification(thread)  # type: ignore[arg-type]
    assert notification is not None
    assert notification.id == 123
    assert notification.kind == "notification"


def test_item_carries_content_for_postprocessor() -> None:
    client = FakeClient([])
    issue_url = "https://api.github.com/repos/a/b/issues/7"
    comment_url = "https://api.github.com/repos/a/b/issues/comments/9"
    client.subjects[issue_url] = GitHubSubject(state="closed", body="issue body")
    client.subjects[comment_url] = GitHubSubject(body="latest comment")
    poller = GitHubPoller(GitHubAccount("pat"), client=client)
    thread = {
        "id": "123",
        "reason": "mention",
        "updated_at": "2026-11-02T10:00:00Z",
        "subject": {
            "title": "标题",
            "type": "Issue",
            "url": issue_url,
            "latest_comment_url": comment_url,
        },
        "repository": {"full_name": "a/b"},
    }
    item = poller._to_item(thread)  # type: ignore[arg-type]
    assert item is not None
    assert item.notification.kind == "closed-issue"
    assert item.reason == "mention"
    assert item.updated_at == "2026-11-02T10:00:00Z"
    assert item.body == "issue body"
    assert item.latest_comment_body == "latest comment"


def test_release_label_and_commit_label() -> None:
    client = FakeClient([])
    release_url = "https://api.github.com/repos/a/b/releases/123"
    commit_url = "https://api.github.com/repos/a/b/commits/abcdef1234567890"
    client.subjects[release_url] = GitHubSubject(
        tag_name="v1.2.3",
        body="release notes",
    )
    client.subjects[commit_url] = GitHubSubject(
        sha="abcdef1234567890",
        body="commit message",
    )
    poller = GitHubPoller(GitHubAccount("pat"), client=client)

    release = poller._to_item(
        {
            "id": "1",
            "subject": {
                "title": "v1.2.3",
                "type": "Release",
                "url": release_url,
            },
            "repository": {"full_name": "a/b"},
        }
    )
    assert release is not None
    assert release.notification.kind == "release"
    assert release.notification.label == "v1.2.3"

    commit = poller._to_item(
        {
            "id": "2",
            "subject": {
                "title": "fix",
                "type": "Commit",
                "url": commit_url,
            },
            "repository": {"full_name": "a/b"},
        }
    )
    assert commit is not None
    assert commit.notification.kind == "commit"
    assert commit.notification.label == "abcdef1"


def test_closed_pull_request_is_not_merged() -> None:
    client = FakeClient([])
    url = "https://api.github.com/repos/a/b/pulls/7"
    client.subjects[url] = GitHubSubject(state="closed", merged=False)
    poller = GitHubPoller(GitHubAccount("pat"), client=client)
    item = poller._to_item(
        {
            "id": "1",
            "subject": {
                "title": "closed pr",
                "type": "PullRequest",
                "url": url,
            },
            "repository": {"full_name": "a/b"},
        }
    )
    assert item is not None
    assert item.notification.kind == "pr-closed"


def test_invalid_thread_is_dead_lettered_and_marked_read(tmp_path: Path) -> None:
    client = FakeClient(
        [
            {"id": "1"},
            {
                "id": "2",
                "subject": {
                    "title": "ok",
                    "type": "Issue",
                    "url": "https://api.github.com/repos/a/b/issues/2",
                },
                "repository": {"full_name": "a/b"},
            },
        ]
    )
    poller = GitHubPoller(
        GitHubAccount("pat"),
        client=client,
        dead_letter=DeadLetterStore(tmp_path / "dead.jsonl"),
    )
    digests = list(poller.poll())
    assert len(digests) == 1
    assert client.marked == ["2", "1"]
    assert "thread 1" in (tmp_path / "dead.jsonl").read_text(encoding="utf-8")


def test_mark_read_failure_does_not_block_others() -> None:
    client = FakeClient(
        [
            {
                "id": "1",
                "subject": {
                    "title": "a",
                    "type": "Issue",
                    "url": "https://api.github.com/repos/a/b/issues/1",
                },
                "repository": {"full_name": "a/b"},
            },
            {
                "id": "2",
                "subject": {
                    "title": "b",
                    "type": "Issue",
                    "url": "https://api.github.com/repos/a/b/issues/2",
                },
                "repository": {"full_name": "a/b"},
            },
        ]
    )
    client.fail_mark.add("1")
    poller = GitHubPoller(GitHubAccount("pat"), client=client)
    list(poller.poll())
    assert client.marked == ["2"]


class _Handler(BaseHTTPRequestHandler):
    retry_count = 0

    def log_message(self, *args: object) -> None:
        pass

    def _send(
        self,
        status: int,
        payload: object = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        port = self.server.server_port
        if self.path.startswith("/notifications?page=2"):
            self._send(200, [{"id": "2"}])
        elif self.path.startswith("/notifications"):
            self._send(
                200,
                [{"id": "1"}],
                {
                    "Link": f'<http://127.0.0.1:{port}/notifications?page=2>; rel="next"',
                    "X-Poll-Interval": "90",
                },
            )
        elif self.path == "/retry":
            type(self).retry_count += 1
            if type(self).retry_count < 3:
                self._send(500, {"message": "boom"})
            else:
                self._send(200, {"ok": True})
        else:
            self._send(404, {})


@pytest.fixture
def api_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_pagination_and_poll_interval(api_url: str) -> None:
    client = GitHubClient("pat", api_url=api_url, sleep=lambda _: None)
    assert client.notifications() == [{"id": "1"}, {"id": "2"}]
    assert client.poll_interval == 90


def test_retry(api_url: str) -> None:
    client = GitHubClient("pat", api_url=api_url, sleep=lambda _: None)
    data, _ = client._request("GET", "/retry")
    assert data == {"ok": True}
    assert _Handler.retry_count == 3


def test_host_restriction(api_url: str) -> None:
    client = GitHubClient("pat", api_url=api_url, sleep=lambda _: None)
    with pytest.raises(GitHubError, match="非 GitHub API 主机"):
        client.subject_state("https://evil.example/repos/a/b/issues/1")
