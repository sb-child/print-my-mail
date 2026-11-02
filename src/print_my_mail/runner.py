"""把邮箱、GitHub 通知和打印机串起来的后台循环。"""

from __future__ import annotations

import logging
import math
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

import grpc

from .client import PrintMyMailClient, print_succeeded
from .config import Config
from .deadletter import DeadLetterStore
from .github import GitHubDigest, GitHubError, GitHubPoller
from .mail import MailError, MailItem, MailPoller
from .postprocess import PostProcessor, TransparentPostProcessor
from .proto import dzsrv_pb2
from .template import make_github_tar, make_mail_tar
from .timeutil import now

log = logging.getLogger(__name__)


class Printer(Protocol):
    """Runner 需要的打印机接口。"""

    def print_typst(self, typst_tar: bytes) -> bytes: ...

    def wait(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
    ) -> dzsrv_pb2.JobStatus: ...


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
        if not math.isfinite(self.poll_interval) or self.poll_interval <= 0:
            raise ValueError("poll_interval 必须是大于 0 的有限数字")
        self.print_timeout = config.print_timeout
        self._print_lock = threading.Lock()
        self._dead_letter = DeadLetterStore(config.dead_letter_file)
        self._mail_pollers = [
            MailPoller(
                account,
                timeout=config.imap_timeout,
                dead_letter=self._dead_letter,
            )
            for account in config.mail
        ]
        self._github_pollers = [
            GitHubPoller(account, dead_letter=self._dead_letter)
            for account in config.github
        ]

    def run_once(self) -> None:
        """轮询一轮后退出。"""
        if not self._has_accounts():
            return
        with PrintMyMailClient(self.config.target) as printer:
            self.poll(printer)

    def run_forever(self) -> None:
        """持续轮询，直到收到 Ctrl-C 或 SIGTERM。"""
        if not self._has_accounts():
            log.warning("没有配置任何邮箱或 GitHub 账户")
            return

        stop = threading.Event()
        previous_sigterm: Any = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda *_: stop.set())

        workers = len(self._mail_pollers) + len(self._github_pollers)
        log.info(
            "开始监听：%d 个邮箱，%d 个 GitHub 账户，间隔 %.0f 秒",
            len(self._mail_pollers),
            len(self._github_pollers),
            self.poll_interval,
        )
        try:
            with PrintMyMailClient(self.config.target) as printer:
                with ThreadPoolExecutor(
                    max_workers=workers,
                    thread_name_prefix="print-my-mail",
                ) as executor:
                    futures = []
                    for poller in self._mail_pollers:
                        futures.append(
                            executor.submit(
                                self._mail_loop,
                                poller,
                                printer,
                                stop,
                            )
                        )
                    for index, poller in enumerate(
                        self._github_pollers, start=1
                    ):
                        futures.append(
                            executor.submit(
                                self._github_loop,
                                poller,
                                printer,
                                stop,
                                index,
                            )
                        )

                    try:
                        while not stop.is_set():
                            if all(future.done() for future in futures):
                                log.error("所有后台任务都已退出")
                                break
                            stop.wait(0.5)
                    except KeyboardInterrupt:
                        log.info("收到中断，停止监听")
                    finally:
                        stop.set()
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)

    def _mail_loop(
        self,
        poller: MailPoller,
        printer: Printer,
        stop: threading.Event,
    ) -> None:
        while not stop.is_set():
            try:
                self._poll_mail(poller, printer)
            except Exception:
                log.exception("邮箱 %s 的后台任务异常", poller.account.user)
                stop.wait(self.poll_interval)
                continue
            stop.wait(self.poll_interval)

    def _github_loop(
        self,
        poller: GitHubPoller,
        printer: Printer,
        stop: threading.Event,
        index: int,
    ) -> None:
        while not stop.is_set():
            try:
                self._poll_github(poller, printer, index)
            except Exception:
                log.exception("第 %d 个 GitHub 账户的后台任务异常", index)
                stop.wait(self.poll_interval)
                continue
            stop.wait(max(self.poll_interval, poller.poll_interval))

    def _has_accounts(self) -> bool:
        return bool(self._mail_pollers or self._github_pollers)

    def poll(self, printer: Printer) -> None:
        """轮询所有账号一次。"""
        for poller in self._mail_pollers:
            self._poll_mail(poller, printer)
        for index, poller in enumerate(self._github_pollers, start=1):
            self._poll_github(poller, printer, index)

    def _poll_mail(self, poller: MailPoller, printer: Printer) -> None:
        try:
            for item in poller.poll():
                processed = self.processor.mail(item)
                if not self._print_mail(printer, processed):
                    return
        except MailError as exc:
            log.warning("邮箱 %s: %s", poller.account.user, exc)
        except Exception:
            log.exception("邮箱 %s 轮询出现未预期错误", poller.account.user)

    def _poll_github(
        self,
        poller: GitHubPoller,
        printer: Printer,
        index: int,
    ) -> None:
        try:
            for digest in poller.poll():
                processed = self.processor.github(digest)
                if not self._print_github(printer, processed):
                    return
        except GitHubError as exc:
            log.warning("第 %d 个 GitHub 账户轮询失败: %s", index, exc)
        except Exception:
            log.exception("第 %d 个 GitHub 账户轮询出现未预期错误", index)

    def _print_mail(self, printer: Printer, item: MailItem) -> bool:
        tar = make_mail_tar(
            recp=item.recipient,
            sender=item.sender,
            t=item.date,
            title=item.subject,
            sender_name=item.sender_name,
            content=item.body,
        )
        label = f"邮件《{item.subject}》"
        if not self._submit(printer, tar, label):
            return False
        log.info("打印完成: %s", label)
        return True

    def _print_github(self, printer: Printer, digest: GitHubDigest) -> bool:
        tar = make_github_tar(
            now(),
            digest.items,
        )
        label = f"{len(digest.items)} 条 GitHub 通知"
        if not self._submit(printer, tar, label):
            return False
        log.info("打印完成: %s", label)
        return True

    def _submit(self, printer: Printer, tar: bytes, label: str) -> bool:
        with self._print_lock:
            try:
                job_id = printer.print_typst(tar)
            except grpc.RpcError as exc:
                log.warning("提交打印失败（%s）: %s", label, exc)
                return False

            try:
                status = printer.wait(job_id, timeout=self.print_timeout)
            except (grpc.RpcError, TimeoutError, LookupError) as exc:
                log.warning("等待打印结果失败（%s）: %s", label, exc)
                return False

            if not print_succeeded(status):
                log.warning(
                    "打印未成功（%s）: job_status=%s printer_status=%s",
                    label,
                    status.job_status,
                    status.printer_status,
                )
                return False
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


def run_once(
    config: Config,
    *,
    processor: PostProcessor | None = None,
    poll_interval: float | None = None,
) -> None:
    """轮询所有账号一次后退出。"""
    Runner(
        config,
        processor=processor,
        poll_interval=poll_interval,
    ).run_once()
