"""命令行入口。

命令：
- `print-my-mail`：持续监听并打印；
- `print-my-mail test`：打印示例页面；
- `print-my-mail once`：只轮询一轮；
- `print-my-mail folders`：列出配置邮箱的真实 IMAP 文件夹名。
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from .client import PrintMyMailClient
from .config import Config, load_config
from .mail import MailError, list_folders
from .runner import run, run_once
from .sample import print_test_page


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="print-my-mail",
        description="通过打印小票提醒你有新邮件或 GitHub 通知",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("test", help="打印一封示例邮件和示例 GitHub 通知")
    subparsers.add_parser("once", help="轮询一轮后退出")
    subparsers.add_parser("folders", help="列出配置邮箱的 IMAP 文件夹")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()

    if args.command == "test":
        _print_test(config)
    elif args.command == "once":
        run_once(config)
    elif args.command == "folders":
        _print_folders(config)
    else:
        run(config)


def _print_folders(config: Config) -> None:
    if not config.mail:
        print("没有配置任何邮箱")
        return

    for account in config.mail:
        print(f"{account.user}:")
        try:
            folders = list_folders(account, timeout=config.imap_timeout)
        except MailError as exc:
            print(f"  读取失败: {exc}")
            continue
        for folder in folders:
            flags = f"  {' '.join(folder.flags)}" if folder.flags else ""
            display = folder.display_name
            decoded = f"  ({display})" if display != folder.name else ""
            print(f"  {folder.name}{flags}{decoded}")

        suggested = [
            folder.name
            for folder in folders
            if folder.name.upper() == "INBOX"
            or "\\Junk" in folder.flags
            or "\\Spam" in folder.flags
        ]
        if not suggested:
            suggested = ["INBOX"]
        values = ", ".join(_toml_string(name) for name in suggested)
        print(f"  推荐配置: folders = [{values}]")


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _print_test(config: Config) -> None:
    with PrintMyMailClient(config.target) as cli:
        print_test_page(cli)
