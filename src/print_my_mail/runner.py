"""把邮箱、GitHub 通知和打印机串起来的后台循环。

- 每个邮箱 / GitHub 账户在独立线程中轮询；
- 打印提交和等待结果串行执行；
- 打印失败时按指数退避重试，超过 `print_max_retries` 后写死信。

用法::

    from print_my_mail import load_config, run, run_once

    run(load_config())       # 持续监听，直到 Ctrl-C 或 SIGTERM
    run_once(load_config())  # 只轮询一轮后退出

投递语义是 at-least-once：打印成功但还来不及标记已读时进程退出，
下次会重复打印一次，但不会静默丢通知。
"""

from __future__ import annotations

import logging
import math
import signal
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

import grpc

from .client import PrintMyMailClient, print_succeeded
from .config import Config, MailAccount
from .deadletter import DeadLetterStore
from .github import GitHubDigest, GitHubError, GitHubPoller
from .mail import MailError, MailItem, MailPoller
from .postprocess import PostProcessor, TransparentPostProcessor
from .proto import dzsrv_pb2
from .template import make_github_tar, make_mail_tar
from .timeutil import now

log = logging.getLogger(__name__)


class MailSource(Protocol):
    """Runner 需要的邮箱轮询接口。"""

    account: MailAccount

    def poll(self) -> Iterator[MailItem]: ...

    def mark_seen(self, item: MailItem) -> None: ...


class GitHubSource(Protocol):
    """Runner 需要的 GitHub 轮询接口。"""

    @property
    def poll_interval(self) -> float: ...

    def poll(self) -> Iterator[GitHubDigest]: ...


class Printer(Protocol):
    """Runner 需要的打印机接口。"""

    def print_typst(
        self,
        typst_tar: bytes,
        *,
        timeout: float | None = None,
    ) -> bytes: ...

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
        self.print_max_retries = config.print_max_retries
        self.print_retry_base_delay = config.print_retry_base_delay
        self.print_retry_max_delay = config.print_retry_max_delay
        self._print_lock = threading.Lock()
        self._dead_letter = DeadLetterStore(config.dead_letter_file)
        self._mail_pollers: list[MailSource] = [
            MailPoller(
                account,
                timeout=config.imap_timeout,
                dead_letter=self._dead_letter,
            )
            for account in config.mail
        ]
        self._github_pollers: list[GitHubSource] = [
            GitHubPoller(
                account,
                dead_letter=self._dead_letter,
                fetch_content=config.github_fetch_content,
            )
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
        poller: MailSource,
        printer: Printer,
        stop: threading.Event,
    ) -> None:
        while not stop.is_set():
            try:
                self._poll_mail(poller, printer, stop)
            except Exception:
                log.exception("邮箱 %s 的后台任务异常", poller.account.user)
                stop.wait(self.poll_interval)
                continue
            stop.wait(self.poll_interval)

    def _github_loop(
        self,
        poller: GitHubSource,
        printer: Printer,
        stop: threading.Event,
        index: int,
    ) -> None:
        while not stop.is_set():
            try:
                self._poll_github(poller, printer, index, stop)
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

    def _poll_mail(
        self,
        poller: MailSource,
        printer: Printer,
        stop: threading.Event | None = None,
    ) -> None:
        try:
            for item in poller.poll():
                processed = self.processor.mail(item)
                attempts = 0
                while True:
                    error = self._print_mail(printer, processed)
                    if error is None:
                        poller.mark_seen(item)
                        break

                    attempts += 1
                    if attempts > self.print_max_retries:
                        self._dead_letter.write(
                            kind="print",
                            account=poller.account.user,
                            item=f"{item.folder} UID {item.uid}",
                            reason=error,
                            details={
                                "folder": item.folder,
                                "uid": item.uid,
                                "subject": item.subject,
                                "attempts": attempts,
                            },
                        )
                        poller.mark_seen(item)
                        break

                    delay = self._retry_delay(attempts)
                    log.warning(
                        "打印失败，第 %d 次重试将在 %.1f 秒后（%s）",
                        attempts,
                        delay,
                        error,
                    )
                    if not self._wait(stop, delay):
                        return
        except MailError as exc:
            log.warning("邮箱 %s: %s", poller.account.user, exc)
        except Exception:
            log.exception("邮箱 %s 轮询出现未预期错误", poller.account.user)

    def _poll_github(
        self,
        poller: GitHubSource,
        printer: Printer,
        index: int,
        stop: threading.Event | None = None,
    ) -> None:
        try:
            for digest in poller.poll():
                processed = self.processor.github(digest)
                attempts = 0
                while True:
                    error = self._print_github(printer, processed)
                    if error is None:
                        break

                    attempts += 1
                    if attempts > self.print_max_retries:
                        self._dead_letter.write(
                            kind="print",
                            account=f"GitHub #{index}",
                            item=", ".join(
                                f"{item.notification.repo}#{item.notification.id}"
                                for item in digest.items
                            ),
                            reason=error,
                            details={
                                "attempts": attempts,
                                "notifications": [
                                    {
                                        "repo": item.notification.repo,
                                        "id": item.notification.id,
                                        "title": item.notification.title,
                                    }
                                    for item in digest.items
                                ],
                            },
                        )
                        break

                    delay = self._retry_delay(attempts)
                    log.warning(
                        "打印失败，第 %d 次重试将在 %.1f 秒后（%s）",
                        attempts,
                        delay,
                        error,
                    )
                    if not self._wait(stop, delay):
                        return
        except GitHubError as exc:
            log.warning("第 %d 个 GitHub 账户轮询失败: %s", index, exc)
        except Exception:
            log.exception("第 %d 个 GitHub 账户轮询出现未预期错误", index)

    def _retry_delay(self, attempts: int) -> float:
        return min(
            self.print_retry_base_delay * (2 ** (attempts - 1)),
            self.print_retry_max_delay,
        )

    def _wait(self, stop: threading.Event | None, delay: float) -> bool:
        if stop is None:
            time.sleep(delay)
            return True
        return not stop.wait(delay)

    def _print_mail(self, printer: Printer, item: MailItem) -> str | None:
        tar = make_mail_tar(
            recp=item.recipient,
            sender=item.sender,
            t=item.date,
            title=item.subject,
            sender_name=item.sender_name,
            content=item.body,
        )
        label = f"邮件《{item.subject}》"
        error = self._submit(printer, tar, label)
        if error is not None:
            return error
        log.info("打印完成: %s", label)
        return None

    def _print_github(self, printer: Printer, digest: GitHubDigest) -> str | None:
        tar = make_github_tar(
            now(),
            digest.items,
        )
        label = f"{len(digest.items)} 条 GitHub 通知"
        error = self._submit(printer, tar, label)
        if error is not None:
            return error
        log.info("打印完成: %s", label)
        return None

    def _submit(self, printer: Printer, tar: bytes, label: str) -> str | None:
        with self._print_lock:
            try:
                job_id = printer.print_typst(tar, timeout=self.print_timeout)
            except grpc.RpcError as exc:
                message = f"提交打印失败: {exc}"
                log.warning("%s（%s）", message, label)
                return message

            try:
                status = printer.wait(job_id, timeout=self.print_timeout)
            except (grpc.RpcError, TimeoutError, LookupError) as exc:
                message = f"等待打印结果失败: {exc}"
                log.warning("%s（%s）", message, label)
                return message

            if not print_succeeded(status):
                message = (
                    "打印未成功: "
                    f"job_status={status.job_status} "
                    f"printer_status={status.printer_status}"
                )
                log.warning("%s（%s）", message, label)
                return message
            return None


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
