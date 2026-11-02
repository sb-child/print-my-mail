"""轮询 GitHub 未读通知。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .config import GitHubAccount
from .template import GitHubKind, GitHubNotification

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "print-my-mail"
REQUEST_TIMEOUT = 30.0
PAGE_SIZE = 50


class GitHubError(RuntimeError):
    """访问 GitHub API 失败。"""


@dataclass(frozen=True)
class GitHubDigest:
    """一次准备打印的 GitHub 通知集合。"""

    notifications: tuple[GitHubNotification, ...]
    content: str = ""


class GitHubClient:
    """最小化的 GitHub REST API 客户端，只包含轮询通知需要的接口。"""

    def __init__(self, pat: str) -> None:
        self._pat = pat

    def notifications(self) -> list[dict[str, Any]]:
        """获取当前未读通知。"""
        data = self._request(
            "GET",
            "/notifications",
            params={"all": "false", "per_page": str(PAGE_SIZE)},
        )
        if not isinstance(data, list):
            raise GitHubError("GitHub 通知接口返回了非数组结果")
        return [item for item in data if isinstance(item, dict)]

    def mark_read(self, thread_id: str) -> None:
        """把一条通知线程标记为已读。"""
        self._request("PATCH", f"/notifications/threads/{thread_id}")

    def subject_state(self, url: str) -> str | None:
        """查询 issue/PR 的 state（open/closed）。"""
        data = self._request("GET", url)
        if isinstance(data, dict):
            state = data.get("state")
            if isinstance(state, str):
                return state
        return None

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else API_URL + path_or_url
        )
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._pat}",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise GitHubError(
                f"GitHub API {method} {url} 返回 {exc.code} {exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitHubError(f"无法访问 GitHub API: {exc}") from exc

        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GitHubError("GitHub API 返回了无效的 JSON") from exc


class GitHubPoller:
    """轮询一个 GitHub 账户的未读通知。"""

    def __init__(self, account: GitHubAccount) -> None:
        self.account = account
        self._client = GitHubClient(account.pat)

    def poll(self) -> Iterator[GitHubDigest]:
        """
        产出未读通知。

        调用方打印成功后继续迭代，才会把这一批通知标记为已读；
        如果中途抛异常，这些通知会保持未读状态。
        """
        threads = self._client.notifications()
        notifications: list[GitHubNotification] = []
        for thread in threads:
            notification = self._to_notification(thread)
            if notification is not None:
                notifications.append(notification)

        if notifications:
            yield GitHubDigest(tuple(notifications))

        for thread in threads:
            thread_id = thread.get("id")
            if isinstance(thread_id, str):
                self._client.mark_read(thread_id)

    def _to_notification(self, thread: dict[str, Any]) -> GitHubNotification | None:
        subject = thread.get("subject")
        repository = thread.get("repository")
        if not isinstance(subject, dict) or not isinstance(repository, dict):
            return None

        repo = repository.get("full_name")
        title = subject.get("title")
        subject_type = subject.get("type")
        url = subject.get("url")
        if not isinstance(repo, str) or not isinstance(title, str):
            return None
        if not isinstance(subject_type, str) or not isinstance(url, str):
            return None

        kind = _notification_kind(subject_type, url, self._client)
        number = _subject_number(url)
        if number is None:
            number = _thread_number(thread.get("id"))
        if number is None:
            log.warning("无法从 GitHub 通知中解析编号: %s", url)
            return None

        return GitHubNotification(repo=repo, id=number, title=title, kind=kind)


def _notification_kind(
    subject_type: str,
    url: str,
    client: GitHubClient,
) -> GitHubKind:
    if subject_type not in ("Issue", "PullRequest"):
        return "notification"

    try:
        state = client.subject_state(url)
    except GitHubError as exc:
        log.warning("查询 GitHub 通知状态失败，按未关闭处理: %s", exc)
        state = None

    closed = state == "closed"
    if subject_type == "Issue":
        return "closed-issue" if closed else "issue"
    return "pr-merged" if closed else "pr"


def _subject_number(url: str) -> int | None:
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


def _thread_number(thread_id: object) -> int | None:
    if isinstance(thread_id, str) and thread_id.isdigit():
        return int(thread_id)
    return None
