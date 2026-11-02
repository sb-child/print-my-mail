"""打印前的后处理钩子。

如果只需要“摘要成 100 字以内”，直接用内置的
`print_my_mail.ai.OpenAIPostProcessor`；需要自定义逻辑时再实现本协议。例如
可以在 `TransparentPostProcessor` 旁边实现一个接 AI 的版本。

实现示例::

    from dataclasses import replace

    from print_my_mail import Runner, TransparentPostProcessor, load_config


    class MyProcessor(TransparentPostProcessor):
        def mail(self, item):
            # 这里可以接 AI 总结
            return item

        def github(self, digest):
            items = []
            for item in digest.items:
                print(item.notification.repo, item.notification.title)
                print(item.reason, item.body, item.latest_comment_body)
                items.append(replace(item, content="可选的 AI 摘要"))
            return replace(digest, items=tuple(items))


    Runner(load_config(), processor=MyProcessor()).run_forever()

默认的 `TransparentPostProcessor` 原样返回内容，不做任何改写。
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
        """
        处理一批 GitHub 通知。

        `digest.items` 里包含标题、原因、issue/PR 正文和最新评论正文，
        可以给每条 item 设置 `content`，打印时会显示在对应通知下面。
        """
        ...


class TransparentPostProcessor:
    """默认实现：原样返回，不做任何处理。"""

    def mail(self, item: MailItem) -> MailItem:
        return item

    def github(self, digest: GitHubDigest) -> GitHubDigest:
        return digest
