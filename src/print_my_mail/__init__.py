import argparse
from collections.abc import Sequence
from datetime import datetime

from .client import DEFAULT_TARGET, PrintMyMailClient
from .config import (
    Config,
    GitHubAccount,
    MailAccount,
    find_config,
    load_config,
    read_config,
)
from .template import GitHubNotification, make_github_tar, make_mail_tar

__all__ = [
    "Config",
    "DEFAULT_TARGET",
    "GitHubAccount",
    "GitHubNotification",
    "MailAccount",
    "PrintMyMailClient",
    "find_config",
    "load_config",
    "print_test_page",
    "main",
    "make_github_tar",
    "make_mail_tar",
    "read_config",
    "run",
]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="print-my-mail",
        description="通过打印小票提醒你有新邮件或 GitHub 通知",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("test", help="运行当前的示例逻辑")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "test":
        _test(cfg)
    else:
        run(cfg)


def _test(cfg: Config) -> None:
    print("Hello from print-my-mail!")
    with PrintMyMailClient(cfg.target) as cli:
        print_test_page(cli)


def run(cfg: Config) -> None:
    """
    正式逻辑入口，目前预留为空。
    """
    pass


def print_test_page(cli: PrintMyMailClient) -> None:
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
