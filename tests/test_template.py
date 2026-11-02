from __future__ import annotations

import io
import tarfile
from datetime import datetime

import pytest

from print_my_mail import GitHubItem, GitHubNotification, make_github_tar, make_mail_tar


def _main_typ(tar: bytes) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        return archive.extractfile("main.typ").read().decode()  # type: ignore[union-attr]


def test_mail_tar_contains_assets_and_rendered_values() -> None:
    tar = make_mail_tar(
        recp="a@example.com",
        sender="b@example.com",
        t=datetime(2026, 11, 2, 12, 34, 56),
        title='标题 "quoted"',
        sender_name="发件人",
        content="正文\\内容",
    )
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        names = set(archive.getnames())
    assert {"main.typ", "mail-dot.svg"} <= names
    text = _main_typ(tar)
    assert "2026/11/02 12:34:56" in text
    assert '标题 \\"quoted\\"' in text
    assert "正文\\\\内容" in text


def test_github_tar_contains_assets_and_items() -> None:
    tar = make_github_tar(
        datetime(2026, 11, 2),
        [
            GitHubItem(
                GitHubNotification("a/b", 1, "标题", "release", label="v1.2.3"),
                content="第一条摘要",
            ),
            GitHubItem(GitHubNotification("c/d", 2, "合并", "pr-merged")),
        ],
    )
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        names = set(archive.getnames())
    assert {"main.typ", "github.svg", "issue.svg", "pr-merged.svg"} <= names
    text = _main_typ(tar)
    assert 'repo: "a/b"' in text
    assert 'content: "第一条摘要"' in text
    assert 'label: "v1.2.3"' in text
    assert 'kind: "pr-merged"' in text


def test_github_tar_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        make_github_tar(
            datetime.now(),
            [GitHubNotification("a/b", 1, "标题", "unknown")],  # type: ignore[arg-type]
        )
