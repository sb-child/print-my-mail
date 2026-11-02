"""Typst 模板渲染和 tar 打包。

- 模板从包内 `templates/` 读取，安装后也能使用；
- 邮件和 GitHub 的时间统一按 UTC+8 输出；
- GitHub 的每条通知通过 `#item(...)` 生成，支持单独的内容字段。
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Iterable, Literal

from .timeutil import to_utc8

_TEMPLATE_NAME = "main.typ"
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

GitHubKind = Literal[
    "issue",
    "closed-issue",
    "pr",
    "pr-merged",
    "pr-closed",
    "release",
    "commit",
    "notification",
]
_GITHUB_KINDS: tuple[GitHubKind, ...] = (
    "issue",
    "closed-issue",
    "pr",
    "pr-merged",
    "pr-closed",
    "release",
    "commit",
    "notification",
)


@dataclass(frozen=True)
class GitHubNotification:
    """
    一条 GitHub 通知。

    `kind` 取值:
    - `issue`: 打开的 issue
    - `closed-issue`: 已关闭的 issue
    - `pr`: 打开的 PR
    - `pr-merged`: 已合并的 PR
    - `pr-closed`: 已关闭但未合并的 PR
    - `release`: Release
    - `commit`: Commit
    - `notification`: 其他类型的 GitHub 通知
    """

    repo: str
    id: int
    title: str
    kind: GitHubKind
    label: str = ""


@dataclass(frozen=True)
class GitHubItem:
    """一条 GitHub 通知及打印/后处理上下文。"""

    notification: GitHubNotification
    reason: str = ""
    updated_at: str = ""
    url: str = ""
    latest_comment_url: str = ""
    body: str = ""
    latest_comment_body: str = ""
    content: str = ""


def make_mail_tar(
    recp: str, sender: str, t: datetime, title: str, sender_name: str, content: str
) -> bytes:
    """
    通过模板 `templates/mail/main.typ` 制作邮件tar包
    """
    template_dir = _template_dir("mail")
    rendered = _render(
        template_dir,
        {
            "recp": _escape_typst_string(recp),
            "sender": _escape_typst_string(sender),
            "time": _escape_typst_string(
                to_utc8(t).strftime("%Y/%m/%d %H:%M:%S")
            ),
            "title": _escape_typst_string(title),
            "sender_name": _escape_typst_string(sender_name),
            "content": _escape_typst_string(content),
        },
    )
    return _make_tar(template_dir, rendered)


def make_github_tar(
    t: datetime,
    items: Iterable[GitHubItem | GitHubNotification],
) -> bytes:
    """
    通过模板 `templates/github/main.typ` 制作 GitHub 通知tar包
    """
    template_dir = _template_dir("github")
    rendered = _render(
        template_dir,
        {
            "time": _escape_typst_string(
                to_utc8(t).strftime("%Y/%m/%d %H:%M:%S")
            ),
            "items": _github_items(items),
        },
    )
    return _make_tar(template_dir, rendered)


def _render(template_dir: Traversable, values: dict[str, str]) -> str:
    template = (template_dir / _TEMPLATE_NAME).read_text(encoding="utf-8")
    return _PLACEHOLDER_RE.sub(lambda match: _value(values, match), template)


def _make_tar(template_dir: Traversable, rendered: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(template_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                continue
            if path.name == _TEMPLATE_NAME:
                data = rendered.encode("utf-8")
            else:
                data = path.read_bytes()
            info = tarfile.TarInfo(path.name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))

    return buffer.getvalue()


def _github_items(
    entries: Iterable[GitHubItem | GitHubNotification],
) -> str:
    chunks = []
    for entry in entries:
        item = entry if isinstance(entry, GitHubItem) else GitHubItem(entry)
        notification = item.notification
        kind = notification.kind
        if kind not in _GITHUB_KINDS:
            raise ValueError(f"未知的 GitHub 通知类型: {kind}")
        chunks.append(
            "#item(\n"
            f'  repo: "{_escape_typst_string(notification.repo)}",\n'
            f"  id: {int(notification.id)},\n"
            f'  title: "{_escape_typst_string(notification.title)}",\n'
            f'  kind: "{kind}",\n'
            f'  label: "{_escape_typst_string(notification.label)}",\n'
            f'  content: "{_escape_typst_string(item.content)}",\n'
            ")"
        )
    return "\n".join(chunks)


def _value(values: dict[str, str], match: re.Match[str]) -> str:
    name = match.group(1)
    try:
        return values[name]
    except KeyError:
        raise ValueError(f"模板中存在未知占位符: {{{{{name}}}}}") from None


def _escape_typst_string(text: str) -> str:
    """转义 Typst 字符串字面量中的特殊字符，换行可以原样保留。"""
    return text.replace("\r\n", "\n").replace("\\", "\\\\").replace('"', '\\"')


def _template_dir(name: str) -> Traversable:
    template_dir = resources.files("print_my_mail.templates") / name
    if not (template_dir / _TEMPLATE_NAME).is_file():
        raise FileNotFoundError(f"找不到模板: {name}/{_TEMPLATE_NAME}")
    return template_dir
