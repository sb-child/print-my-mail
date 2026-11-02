"""把邮箱、GitHub 通知和打印机串起来的后台循环。"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Protocol

import grpc

from .client import PrintMyMailClient
from .config import Config
from .github import GitHubDigest, GitHubError, GitHubPoller
from .mail import MailError, MailItem, MailPoller
from .postprocess import PostProcessor, TransparentPostProcessor
from .template import make_github_tar, make_mail_tar

log = logging.getLogger(__name__)


class Printer(Protocol):
    """Runner 需要的打印机接口。"""

    def print_typst(self, typst_tar: bytes) -> bytes: ...


class Runner:
    """轮询所有账号，并在收到新通知时提交打印。"""

    def __init__(
        self,
        config: Config,
        *,
        processor: PostProcessor | None = None,
        poll_interval: float | None = None,
    ) -> None:
        self.config = config
        self.processor = processor or TransparentPostProcessor()
        self.poll_interval = (
            config.poll_interval if poll_interval is None else poll_interval
        )
        self._mail_pollers = [MailPoller(account) for account in config.mail]
        self._github_pollers = [GitHubPoller(account) for account in config.github]

    def run_once(self) -> None:
        """轮询一轮后退出。"""
        if not self._has_accounts():
            return
        with PrintMyMailClient(self.config.target) as printer:
            self.poll(printer)

    def run_forever(self) -> None:
        """持续轮询，直到收到 Ctrl-C。"""
        if not self._has_accounts():
            log.warning("没有配置任何邮箱或 GitHub 账户")
            return

        log.info(
            "开始监听：%d 个邮箱，%d 个 GitHub 账户，间隔 %.0f 秒",
            len(self._mail_pollers),
            len(self._github_pollers),
            self.poll_interval,
        )
        with PrintMyMailClient(self.config.target) as printer:
            try:
                while True:
                    self.poll(printer)
                    time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                log.info("收到中断，停止监听")

    def _has_accounts(self) -> bool:
        return bool(self._mail_pollers or self._github_pollers)

    def poll(self, printer: Printer) -> None:
        """轮询所有账号一次。"""
        for poller in self._mail_pollers:
            self._poll_mail(poller, printer)
        for poller in self._github_pollers:
            self._poll_github(poller, printer)

    def _poll_mail(self, poller: MailPoller, printer: Printer) -> None:
        try:
            for item in poller.poll():
                processed = self.processor.mail(item)
                if not self._print_mail(printer, processed):
                    return
        except MailError as exc:
            log.warning("%s", exc)

    def _poll_github(self, poller: GitHubPoller, printer: Printer) -> None:
        try:
            for digest in poller.poll():
                processed = self.processor.github(digest)
                if not self._print_github(printer, processed):
                    return
        except GitHubError as exc:
            log.warning("%s", exc)

    def _print_mail(self, printer: Printer, item: MailItem) -> bool:
        tar = make_mail_tar(
            recp=item.recipient,
            sender=item.sender,
            t=item.date,
            title=item.subject,
            sender_name=item.sender_name,
            content=item.body,
        )
        try:
            printer.print_typst(tar)
        except grpc.RpcError as exc:
            log.warning("提交邮件打印失败（%s）: %s", item.subject, exc)
            return False
        log.info("已提交邮件打印: %s", item.subject)
        return True

    def _print_github(self, printer: Printer, digest: GitHubDigest) -> bool:
        tar = make_github_tar(
            datetime.now(),
            digest.notifications,
            content=digest.content,
        )
        try:
            printer.print_typst(tar)
        except grpc.RpcError as exc:
            log.warning("提交 GitHub 通知打印失败: %s", exc)
            return False
        log.info("已提交 %d 条 GitHub 通知打印", len(digest.notifications))
        return True


def run(
    config: Config,
    *,
    processor: PostProcessor | None = None,
    poll_interval: float | None = None,
) -> None:
    """正式入口：监听配置中的账号并打印通知。"""
    Runner(
        config,
        processor=processor,
        poll_interval=poll_interval,
    ).run_forever()
