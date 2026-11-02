"""打印前的后处理钩子。

以后可以在 `TransparentPostProcessor` 旁边实现一个接 AI 的版本，
例如总结邮件正文、给 GitHub 通知生成摘要。
"""

from __future__ import annotations

from typing import Protocol

from .github import GitHubDigest
from .mail import MailItem


class PostProcessor(Protocol):
    """打印前对通知内容做处理。"""

    def mail(self, item: MailItem) -> MailItem:
        """处理一封邮件，返回准备打印的内容。"""
        ...

    def github(self, digest: GitHubDigest) -> GitHubDigest:
        """处理一批 GitHub 通知，返回准备打印的内容。"""
        ...


class TransparentPostProcessor:
    """默认实现：原样返回，不做任何处理。"""

    def mail(self, item: MailItem) -> MailItem:
        return item

    def github(self, digest: GitHubDigest) -> GitHubDigest:
        return digest
