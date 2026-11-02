"""命令行入口。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from .client import PrintMyMailClient
from .config import Config, load_config
from .runner import run
from .sample import print_test_page


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="print-my-mail",
        description="通过打印小票提醒你有新邮件或 GitHub 通知",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("test", help="打印一封示例邮件和示例 GitHub 通知")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()

    if args.command == "test":
        _print_test(config)
    else:
        run(config)


def _print_test(config: Config) -> None:
    with PrintMyMailClient(config.target) as cli:
        print_test_page(cli)
