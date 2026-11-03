"""命令行入口。

命令：

- `print-my-mail`：持续监听并打印；
- `print-my-mail test`：打印示例页面；
- `print-my-mail once`：只轮询一轮；
- `print-my-mail folders`：列出配置邮箱的真实 IMAP 文件夹名。

用法::

    uv run print-my-mail          # 持续监听
    uv run print-my-mail test     # 打印示例邮件和示例 GitHub 通知
    uv run print-my-mail once     # 只轮询一轮，便于排障
    uv run print-my-mail folders  # 列出真实 IMAP 文件夹名
    uv run print-my-mail -v once  # 输出调试日志（-v 可放在命令前后）

`folders` 输出示例::

    sbchild0@gmail.com:
      INBOX  \\HasNoChildren
      [Gmail]/&V4NXPpCuTvY-  \\HasNoChildren \\Junk  ([Gmail]/垃圾邮件)
      推荐配置: folders = ["INBOX", "[Gmail]/&V4NXPpCuTvY-"]

把“推荐配置”那一行直接复制到 `config.toml` 的 `[[mail]]` 下面即可。
括号里是解码后的展示名，配置时必须使用左边的原始编码名。
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from .client import PrintMyMailClient
from .config import Config, find_config, load_config
from .mail import MailError, list_folders
from .runner import run, run_once
from .sample import print_test_page

log = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出调试日志",
    )
    parser = argparse.ArgumentParser(
        prog="print-my-mail",
        description="通过打印小票提醒你有新邮件或 GitHub 通知",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "test",
        parents=[common],
        help="打印一封示例邮件和示例 GitHub 通知",
    )
    subparsers.add_parser(
        "once",
        parents=[common],
        help="轮询一轮后退出",
    )
    subparsers.add_parser(
        "folders",
        parents=[common],
        help="列出配置邮箱的 IMAP 文件夹",
    )

    args = parser.parse_args(argv)
    verbose = getattr(args, "verbose", False)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=(
            "%(asctime)s %(levelname)-7s "
            "[%(threadName)s] %(name)s: %(message)s"
        ),
    )
    log.info("命令: %s", args.command or "run")
    config_path = find_config()
    log.info("配置文件: %s", config_path or "未找到，使用默认配置")
    try:
        config = load_config()
    except (ValueError, FileNotFoundError) as exc:
        log.error("加载配置失败: %s", exc)
        raise SystemExit(1) from None

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
