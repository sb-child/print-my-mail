"""轮询 GitHub 未读通知。

约定：
- 自动分页、重试，并遵守 GitHub 的限流相关响应头；
- 通知类型映射为 issue / pr / release / commit 等展示类型；
- Release 右上角显示 tag 版本，Commit 显示短 SHA；
- `GitHubItem` 保留正文、最新评论正文、reason 等上下文给 PostProcessor。
"""

from __future__ import annotations

import email.utils
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import GitHubAccount
from .deadletter import DeadLetterStore
from .template import GitHubItem, GitHubKind, GitHubNotification
from .timeutil import iso_to_utc8

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "print-my-mail"
REQUEST_TIMEOUT = 30.0
PAGE_SIZE = 50
MAX_PAGES = 20
MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHubError(RuntimeError):
    """访问 GitHub API 失败。"""


@dataclass(frozen=True)
class GitHubSubject:
    """GitHub issue/PR/评论等接口返回的正文信息。"""

    state: str | None = None
    body: str = ""
    merged: bool = False
    tag_name: str = ""
    sha: str = ""


class GitHubAPI(Protocol):
    """GitHubPoller 需要的最小客户端接口。"""

    poll_interval: float

    def notifications(self) -> list[dict[str, Any]]: ...

    def subject(self, url: str) -> GitHubSubject: ...

    def mark_read(self, thread_id: str) -> None: ...


@dataclass(frozen=True)
class GitHubDigest:
    """一次准备打印的 GitHub 通知集合。"""

    items: tuple[GitHubItem, ...]

    @property
    def notifications(self) -> tuple[GitHubNotification, ...]:
        return tuple(item.notification for item in self.items)


class GitHubClient:
    """最小化的 GitHub REST API 客户端，只包含轮询通知需要的接口。"""

    def __init__(
        self,
        pat: str,
        *,
        api_url: str = API_URL,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        self._pat = pat
        self._api_url = api_url.rstrip("/")
        host = urlparse(self._api_url).hostname
        if not host:
            raise ValueError(f"无效的 GitHub API 地址: {api_url}")
        self._api_host = host
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_modified: str | None = None
        self.poll_interval = 60.0

    def notifications(self) -> list[dict[str, Any]]:
        """获取当前未读通知，自动处理分页。"""
        items: list[dict[str, Any]] = []
        url = "/notifications"
        params: dict[str, str] | None = {
            "all": "false",
            "per_page": str(PAGE_SIZE),
        }
        headers: Mapping[str, str] | None = None
        if self._last_modified:
            headers = {"If-Modified-Since": self._last_modified}

        for page in range(1, MAX_PAGES + 1):
            data, response_headers = self._request(
                "GET",
                url,
                params=params,
                headers=headers,
            )
            self._update_poll_interval(response_headers)

            if data is None:
                # 304 Not Modified。
                return []

            if not isinstance(data, list):
                raise GitHubError("GitHub 通知接口返回了非数组结果")
            items.extend(item for item in data if isinstance(item, dict))

            last_modified = response_headers.get("Last-Modified")
            if last_modified:
                self._last_modified = last_modified

            next_url = _next_link(response_headers)
            if next_url is None:
                return items

            # 后续分页不需要再带条件请求头和初始参数。
            url = next_url
            params = None
            headers = None

        log.warning("GitHub 通知超过 %d 页，剩余通知下一轮再处理", MAX_PAGES)
        return items

    def mark_read(self, thread_id: str) -> None:
        """把一条通知线程标记为已读。"""
        self._request("PATCH", f"/notifications/threads/{thread_id}")

    def subject(self, url: str) -> GitHubSubject:
        """查询 issue/PR/评论等资源，返回 state 和正文。"""
        data, _ = self._request("GET", url)
        if not isinstance(data, dict):
            return GitHubSubject()
        state = data.get("state")
        body = data.get("body")
        merged_at = data.get("merged_at")
        tag_name = data.get("tag_name")
        sha = data.get("sha")
        merged = data.get("merged")
        if not isinstance(merged, bool):
            merged = isinstance(merged_at, str) and bool(merged_at)
        return GitHubSubject(
            state=state if isinstance(state, str) else None,
            body=body if isinstance(body, str) else "",
            merged=merged,
            tag_name=tag_name if isinstance(tag_name, str) else "",
            sha=sha if isinstance(sha, str) else "",
        )

    def subject_state(self, url: str) -> str | None:
        """查询 issue/PR 的 state（open/closed）。"""
        return self.subject(url).state

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, Any]:
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else self._api_url + path_or_url
        )
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        self._validate_url(url)

        for attempt in range(self._max_retries + 1):
            request_headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._pat}",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": API_VERSION,
            }
            if headers:
                request_headers.update(headers)
            request = urllib.request.Request(
                url,
                method=method,
                headers=request_headers,
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=REQUEST_TIMEOUT
                ) as response:
                    body = response.read()
                    return _decode_body(body), response.headers
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return None, exc.headers
                if attempt < self._max_retries and _is_retryable(exc):
                    delay = _retry_delay(exc.headers, attempt)
                    log.warning(
                        "GitHub API %s %s 返回 %s，%.1f 秒后重试",
                        method,
                        url,
                        exc.code,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise GitHubError(
                    f"GitHub API {method} {url} 返回 {exc.code} {exc.reason}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._max_retries:
                    delay = _retry_delay(None, attempt)
                    log.warning(
                        "访问 GitHub API 失败，%.1f 秒后重试: %s",
                        delay,
                        exc,
                    )
                    self._sleep(delay)
                    continue
                raise GitHubError(f"无法访问 GitHub API: {exc}") from exc

        raise GitHubError("GitHub API 重试次数耗尽")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise GitHubError(f"拒绝访问无效的 GitHub URL: {url}")
        if parsed.hostname != self._api_host:
            raise GitHubError(f"拒绝访问非 GitHub API 主机: {parsed.hostname}")
        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
            raise GitHubError(f"拒绝通过明文 HTTP 访问 GitHub API: {url}")

    def _update_poll_interval(self, headers: Any) -> None:
        value = headers.get("X-Poll-Interval")
        if isinstance(value, str) and value.isdigit():
            self.poll_interval = float(value)


class GitHubPoller:
    """轮询一个 GitHub 账户的未读通知。"""

    def __init__(
        self,
        account: GitHubAccount,
        *,
        client: GitHubAPI | None = None,
        dead_letter: DeadLetterStore | None = None,
        fetch_content: bool = True,
    ) -> None:
        self.account = account
        self._client = client or GitHubClient(account.pat)
        self._dead_letter = dead_letter
        self.fetch_content = fetch_content

    @property
    def poll_interval(self) -> float:
        return self._client.poll_interval

    def poll(self) -> Iterator[GitHubDigest]:
        """
        产出未读通知。

        调用方打印成功并确认后继续迭代，才会把这一批通知标记为已读；
        如果中途抛异常，这些通知会保持未读状态。
        """
        threads = self._client.notifications()
        items: list[GitHubItem] = []
        accepted_ids: list[str] = []
        invalid_ids: list[str] = []
        for thread in threads:
            item = self._to_item(thread)
            if item is None:
                thread_id = thread.get("id")
                subject = thread.get("subject")
                details: dict[str, object] = {"thread_id": thread_id}
                if isinstance(subject, dict):
                    details["subject_type"] = subject.get("type")
                    details["title"] = subject.get("title")
                    details["url"] = subject.get("url")
                log.warning("无法处理 GitHub 通知 thread=%s", thread_id)
                if self._dead_letter:
                    self._dead_letter.write(
                        kind="github",
                        account="GitHub",
                        item=f"thread {thread_id}",
                        reason="通知缺少必要字段或无法解析",
                        details=details,
                    )
                if isinstance(thread_id, str):
                    invalid_ids.append(thread_id)
                continue
            items.append(item)
            thread_id = thread.get("id")
            if isinstance(thread_id, str):
                accepted_ids.append(thread_id)

        if items:
            yield GitHubDigest(tuple(items))

        for thread_id in accepted_ids + invalid_ids:
            try:
                self._client.mark_read(thread_id)
            except GitHubError as exc:
                log.warning("标记 GitHub 通知 %s 已读失败: %s", thread_id, exc)

    def _to_notification(self, thread: dict[str, Any]) -> GitHubNotification | None:
        item = self._to_item(thread)
        return item.notification if item else None

    def _to_item(self, thread: dict[str, Any]) -> GitHubItem | None:
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
        if not isinstance(subject_type, str):
            return None
        if url is not None and not isinstance(url, str):
            return None

        url = url or ""
        subject_info = GitHubSubject()
        if url:
            try:
                subject_info = self._client.subject(url)
            except GitHubError as exc:
                log.warning("查询 GitHub 通知正文失败，按未关闭处理: %s", exc)

        if not self.fetch_content:
            subject_info = GitHubSubject(
                state=subject_info.state,
                merged=subject_info.merged,
                tag_name=subject_info.tag_name,
                sha=subject_info.sha,
            )

        kind = _notification_kind(subject_type, subject_info)
        number = _subject_number(url)
        if number is None:
            number = _thread_number(thread.get("id"))
        if number is None:
            log.warning("无法从 GitHub 通知中解析编号: thread=%s", thread.get("id"))
            return None

        label = ""
        if kind == "release":
            label = subject_info.tag_name
        elif kind == "commit":
            label = _short_sha(subject_info.sha or url)

        latest_comment_url = subject.get("latest_comment_url")
        if not isinstance(latest_comment_url, str):
            latest_comment_url = ""

        latest_comment_body = ""
        if self.fetch_content and latest_comment_url and latest_comment_url != url:
            try:
                latest_comment_body = self._client.subject(
                    latest_comment_url
                ).body
            except GitHubError as exc:
                log.warning("查询 GitHub 通知评论失败: %s", exc)

        reason = thread.get("reason")
        updated_at = thread.get("updated_at")

        return GitHubItem(
            notification=GitHubNotification(
                repo=repo,
                id=number,
                title=title,
                kind=kind,
                label=label,
            ),
            reason=reason if isinstance(reason, str) else "",
            updated_at=(
                iso_to_utc8(updated_at)
                if isinstance(updated_at, str)
                else ""
            ),
            url=url,
            latest_comment_url=latest_comment_url,
            body=subject_info.body,
            latest_comment_body=latest_comment_body,
        )


def _notification_kind(
    subject_type: str,
    subject: GitHubSubject,
) -> GitHubKind:
    if subject_type == "Issue":
        return "closed-issue" if subject.state == "closed" else "issue"
    if subject_type == "PullRequest":
        if subject.state == "closed":
            return "pr-merged" if subject.merged else "pr-closed"
        return "pr"
    if subject_type == "Release":
        return "release"
    if subject_type == "Commit":
        return "commit"
    return "notification"


def _short_sha(value: str) -> str:
    if not value:
        return ""
    sha = value.rstrip("/").rsplit("/", 1)[-1]
    return sha[:7]


def _subject_number(url: str | None) -> int | None:
    if url is None:
        return None
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


def _thread_number(thread_id: object) -> int | None:
    if isinstance(thread_id, str) and thread_id.isdigit():
        return int(thread_id)
    return None


def _decode_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GitHubError("GitHub API 返回了无效的 JSON") from exc


def _next_link(headers: Any) -> str | None:
    value = headers.get("Link")
    if not isinstance(value, str):
        return None
    match = _LINK_NEXT_RE.search(value)
    return match.group(1) if match else None


def _is_retryable(exc: urllib.error.HTTPError) -> bool:
    if exc.code in RETRYABLE_STATUSES:
        return True
    return (
        exc.code == 403
        and exc.headers is not None
        and exc.headers.get("X-RateLimit-Remaining") == "0"
    )


def _retry_delay(headers: Any, attempt: int) -> float:
    retry_after = _header_int(headers, "Retry-After")
    if retry_after is not None:
        return min(float(retry_after), MAX_RETRY_DELAY)

    reset = _header_int(headers, "X-RateLimit-Reset")
    if reset is not None:
        remaining = reset - time.time()
        if remaining > 0:
            return min(remaining, MAX_RETRY_DELAY)

    delay = BASE_RETRY_DELAY * (2**attempt) + random.uniform(0, 1)
    return min(delay, MAX_RETRY_DELAY)


def _header_int(headers: Any, name: str) -> int | None:
    if headers is None:
        return None
    value = headers.get(name)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if name == "Retry-After" and isinstance(value, str):
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(int((parsed - datetime.now(timezone.utc)).total_seconds()), 0)
    return None
