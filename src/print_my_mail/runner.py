"""把邮箱、GitHub 通知和打印机串起来的后台循环。

- 每个邮箱 / GitHub 账户在独立 daemon 线程中轮询；
- 打印提交和等待结果串行执行；
- 打印失败时按指数退避重试，超过 `print_max_retries` 后写死信；
- Ctrl-C 一次就退出，不再等待后台任务收尾；
- 启动前的邮件只保留最近 `mail_max_age` 秒，避免打印历史积压。

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
from typing import Any, Protocol
from urllib.parse import urlparse

import grpc

from .ai import OpenAIPostProcessor
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

    label: str

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
        self.processor = processor or (
            OpenAIPostProcessor(config.ai)
            if config.ai.enabled
            else TransparentPostProcessor()
        )
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
                max_age=config.mail_max_age,
                dead_letter=self._dead_letter,
            )
            for account in config.mail
        ]
        self._github_pollers: list[GitHubSource] = [
            GitHubPoller(
                account,
                label=f"GitHub #{index}",
                poll_interval=config.github_poll_interval,
                dead_letter=self._dead_letter,
                fetch_content=config.github_fetch_content,
            )
            for index, account in enumerate(config.github, start=1)
        ]

    def _log_summary(self) -> None:
        config = self.config
        log.info("打印服务: %s", config.target)
        log.info(
            "轮询间隔: %.0f 秒；打印超时: %.0f 秒；"
            "重试: %d 次（%.0f→%.0f 秒）",
            self.poll_interval,
            self.print_timeout,
            self.print_max_retries,
            self.print_retry_base_delay,
            self.print_retry_max_delay,
        )
        log.info("死信文件: %s", config.dead_letter_file)
        if config.mail:
            for account in config.mail:
                log.info(
                    "邮箱: %s @ %s:%d，文件夹: %s，历史阈值: %.0f 秒",
                    account.user,
                    account.host,
                    account.port,
                    ", ".join(account.folders),
                    config.mail_max_age,
                )
        else:
            log.info("邮箱: 未配置")
        if config.github:
            log.info(
                "GitHub: %d 个账户，初始轮询: %.0f 秒，抓取正文/评论: %s",
                len(config.github),
                config.github_poll_interval,
                "是" if config.github_fetch_content else "否",
            )
        else:
            log.info("GitHub: 未配置")
        if config.ai.enabled:
            host = urlparse(config.ai.base_url).hostname or "(无效地址)"
            log.info(
                "AI 摘要: 启用，endpoint=%s，model=%s，"
                "max_chars=%d，token=%s",
                host,
                config.ai.model,
                config.ai.max_chars,
                "已配置" if config.ai.token else "未配置",
            )
        else:
            log.info("AI 摘要: 禁用")

    def run_once(self) -> None:
        """轮询一轮后退出。"""
        if not self._has_accounts():
            log.warning("没有配置任何邮箱或 GitHub 账户")
            return
        self._log_summary()
        log.info("开始单轮轮询")
        with PrintMyMailClient(self.config.target) as printer:
            self.poll(printer)
        log.info("单轮轮询完成")

    def run_forever(self) -> None:
        """持续轮询，直到收到 Ctrl-C 或 SIGTERM。"""
        if not self._has_accounts():
            log.warning("没有配置任何邮箱或 GitHub 账户")
            return

        stop = threading.Event()
        previous_sigterm: Any = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.getsignal(signal.SIGTERM)

            def _on_sigterm(*_: Any) -> None:
                log.info("收到 SIGTERM，停止监听")
                stop.set()

            signal.signal(signal.SIGTERM, _on_sigterm)

        self._log_summary()
        threads: list[threading.Thread] = []
        try:
            with PrintMyMailClient(self.config.target) as printer:
                for index, poller in enumerate(self._mail_pollers, start=1):
                    threads.append(
                        threading.Thread(
                            target=self._mail_loop,
                            args=(poller, printer, stop),
                            name=f"print-my-mail-mail-{index}",
                            daemon=True,
                        )
                    )
                for index, poller in enumerate(
                    self._github_pollers, start=1
                ):
                    threads.append(
                        threading.Thread(
                            target=self._github_loop,
                            args=(poller, printer, stop, index),
                            name=f"print-my-mail-github-{index}",
                            daemon=True,
                        )
                    )
                for thread in threads:
                    log.debug("启动后台线程: %s", thread.name)
                    thread.start()
                log.info("后台线程已启动: %d 个", len(threads))

                try:
                    while not stop.is_set():
                        if all(not thread.is_alive() for thread in threads):
                            log.error("所有后台任务都已退出")
                            break
                        stop.wait(0.5)
                except KeyboardInterrupt:
                    log.info("收到中断，立即停止监听")
                finally:
                    stop.set()
                log.info("监听已停止")
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
            log.debug("邮箱 %s 开始轮询", poller.account.user)
            try:
                self._poll_mail(poller, printer, stop)
            except Exception:
                log.exception("邮箱 %s 的后台任务异常", poller.account.user)
                stop.wait(self.poll_interval)
                continue
            log.debug(
                "邮箱 %s 轮询完成，%.0f 秒后继续",
                poller.account.user,
                self.poll_interval,
            )
            stop.wait(self.poll_interval)

    def _github_loop(
        self,
        poller: GitHubSource,
        printer: Printer,
        stop: threading.Event,
        index: int,
    ) -> None:
        while not stop.is_set():
            log.debug("%s 开始轮询", poller.label)
            try:
                self._poll_github(poller, printer, index, stop)
            except Exception:
                log.exception("%s 的后台任务异常", poller.label)
                stop.wait(self.poll_interval)
                continue
            interval = max(self.poll_interval, poller.poll_interval)
            log.debug(
                "%s 轮询完成，%.0f 秒后继续",
                poller.label,
                interval,
            )
            stop.wait(interval)

    def _has_accounts(self) -> bool:
        return bool(self._mail_pollers or self._github_pollers)

    def poll(self, printer: Printer) -> None:
        """轮询所有账号一次。"""
        log.debug("开始轮询所有账号")
        for poller in self._mail_pollers:
            self._poll_mail(poller, printer)
        for index, poller in enumerate(self._github_pollers, start=1):
            self._poll_github(poller, printer, index)
        log.debug("本轮轮询结束")

    def _poll_mail(
        self,
        poller: MailSource,
        printer: Printer,
        stop: threading.Event | None = None,
    ) -> None:
        try:
            log.debug("邮箱 %s 拉取未读邮件", poller.account.user)
            for item in poller.poll():
                log.info(
                    "邮箱 %s 发现邮件: 文件夹 %s UID=%s "
                    "发件人=%s <%s> 主题=%s",
                    poller.account.user,
                    item.folder,
                    item.uid,
                    item.sender_name,
                    item.sender,
                    item.subject,
                )
                processed = self.processor.mail(item)
                attempts = 0
                while True:
                    error = self._print_mail(printer, processed)
                    if error is None:
                        poller.mark_seen(item)
                        break

                    attempts += 1
                    if attempts > self.print_max_retries:
                        log.error(
                            "邮件《%s》重试耗尽（%d 次），写入死信",
                            item.subject,
                            attempts,
                        )
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
                        "邮件《%s》打印失败，第 %d/%d 次重试将在 "
                        "%.1f 秒后: %s",
                        item.subject,
                        attempts,
                        self.print_max_retries,
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
                log.info(
                    "%s 发现 %d 条通知: %s",
                    poller.label,
                    len(digest.items),
                    ", ".join(
                        f"{item.notification.repo}#{item.notification.id} "
                        f"[{item.notification.kind}] "
                        f"{item.notification.title}"
                        for item in digest.items
                    ),
                )
                processed = self.processor.github(digest)
                attempts = 0
                while True:
                    error = self._print_github(printer, processed)
                    if error is None:
                        break

                    attempts += 1
                    if attempts > self.print_max_retries:
                        log.error(
                            "%s 打印重试耗尽（%d 次），写入死信",
                            poller.label,
                            attempts,
                        )
                        self._dead_letter.write(
                            kind="print",
                            account=poller.label,
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
                        "%s 打印失败，第 %d/%d 次重试将在 "
                        "%.1f 秒后: %s",
                        poller.label,
                        attempts,
                        self.print_max_retries,
                        delay,
                        error,
                    )
                    if not self._wait(stop, delay):
                        return
        except GitHubError as exc:
            log.warning("%s 轮询失败: %s", poller.label, exc)
        except Exception:
            log.exception("%s 轮询出现未预期错误", poller.label)

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
        return self._submit(printer, tar, label)

    def _print_github(self, printer: Printer, digest: GitHubDigest) -> str | None:
        tar = make_github_tar(
            now(),
            digest.items,
        )
        label = f"{len(digest.items)} 条 GitHub 通知"
        return self._submit(printer, tar, label)

    def _submit(self, printer: Printer, tar: bytes, label: str) -> str | None:
        with self._print_lock:
            started = time.monotonic()
            log.info("提交打印: %s", label)
            try:
                job_id = printer.print_typst(tar, timeout=self.print_timeout)
            except grpc.RpcError as exc:
                elapsed = time.monotonic() - started
                message = f"提交打印失败: {exc}"
                log.warning(
                    "%s（%s，用时 %.2f 秒）",
                    message,
                    label,
                    elapsed,
                )
                return message

            log.debug("打印任务已提交: %s job=%s", label, job_id.hex())
            try:
                status = printer.wait(job_id, timeout=self.print_timeout)
            except (grpc.RpcError, TimeoutError, LookupError) as exc:
                elapsed = time.monotonic() - started
                message = f"等待打印结果失败: {exc}"
                log.warning(
                    "%s（%s，job=%s，用时 %.2f 秒）",
                    message,
                    label,
                    job_id.hex(),
                    elapsed,
                )
                return message

            elapsed = time.monotonic() - started
            if not print_succeeded(status):
                message = (
                    "打印未成功: "
                    f"job_status={_status_name(status, 'job_status')} "
                    f"printer_status={_status_name(status, 'printer_status')}"
                )
                log.warning(
                    "%s（%s，job=%s，用时 %.2f 秒）",
                    message,
                    label,
                    job_id.hex(),
                    elapsed,
                )
                return message

            log.info(
                "打印完成: %s job=%s 用时=%.2f 秒",
                label,
                job_id.hex(),
                elapsed,
            )
            return None


def _status_name(status: Any, field_name: str) -> str:
    """把 protobuf 枚举值格式化成 `NAME(value)`，便于看日志。"""
    value = getattr(status, field_name)
    field = status.DESCRIPTOR.fields_by_name.get(field_name)
    enum = field.enum_type if field is not None else None
    descriptor = enum.values_by_number.get(value) if enum is not None else None
    name = descriptor.name if descriptor is not None else "UNKNOWN"
    return f"{name}({value})"


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
