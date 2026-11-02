"""用于验证打印机和模板的示例页面。"""

from __future__ import annotations

from datetime import datetime

from .client import PrintMyMailClient
from .template import GitHubNotification, make_github_tar, make_mail_tar


def print_test_page(cli: PrintMyMailClient) -> None:
    """打印一封示例邮件和一条示例 GitHub 通知。"""
    tar = make_mail_tar(
        recp="someone@example.com",
        sender="sender@example.com",
        t=datetime.now(),
        title="邮件标题",
        sender_name="发件人",
        content="正文内容",
    )
    cli.print_typst(tar)
    tar = make_github_tar(
        datetime.now(),
        [
            GitHubNotification(
                repo="sb-child/print-my-mail",
                id=1,
                title="修复 GitHub 通知模板",
                kind="issue",
            ),
            GitHubNotification(
                repo="sb-child/dz-print",
                id=2,
                title="支持 Typst 渲染",
                kind="pr-merged",
            ),
        ],
        content="可选的通知摘要",
    )
    cli.print_typst(tar)
