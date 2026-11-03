from __future__ import annotations

import io
import logging
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import grpc
import pytest

from print_my_mail import (
    AIConfig,
    Config,
    GitHubAccount,
    GitHubDigest,
    GitHubItem,
    GitHubNotification,
    MailAccount,
    MailItem,
)
from print_my_mail.client import print_succeeded
from print_my_mail.mail import MailPoller
from print_my_mail.proto import dzsrv_pb2
from print_my_mail.runner import Runner


def _status(
    job: Any = dzsrv_pb2.STATUS_PRINT_COMPLETED,
    printer: Any = dzsrv_pb2.PS_PRINTABLE,
) -> dzsrv_pb2.JobStatus:
    return dzsrv_pb2.JobStatus(
        exists=True,
        job_status=job,
        printer_status=printer,
    )


class FakeMailPoller:
    def __init__(self) -> None:
        self.account = MailAccount(
            "me@example.com",
            "pw",
            "imap.example.com",
        )
        self.seen: list[str] = []

    def poll(self):
        yield MailItem(
            "me@example.com",
            "sender@example.com",
            "发件人",
            "主题",
            datetime.now(),
            "正文",
            folder="INBOX",
            uid="1",
        )

    def mark_seen(self, item: MailItem) -> None:
        self.seen.append(item.subject)


class FakeGitHubPoller:
    def __init__(self) -> None:
        self.label = "GitHub #1"
        self.poll_interval = 60.0
        self.marked = False

    def poll(self):
        yield GitHubDigest(
            (GitHubItem(GitHubNotification("a/b", 1, "标题", "issue")),),
        )
        self.marked = True


class FakePrinter:
    def __init__(
        self,
        status: dzsrv_pb2.JobStatus | None = None,
        *,
        submit_error: Exception | None = None,
        wait_error: Exception | None = None,
    ) -> None:
        self.status = status or _status()
        self.submit_error = submit_error
        self.wait_error = wait_error
        self.tars: list[bytes] = []
        self.submit_timeouts: list[float | None] = []

    def print_typst(
        self,
        typst_tar: bytes,
        *,
        timeout: float | None = None,
    ) -> bytes:
        if self.submit_error:
            raise self.submit_error
        self.tars.append(typst_tar)
        self.submit_timeouts.append(timeout)
        return b"job"

    def wait(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
    ) -> dzsrv_pb2.JobStatus:
        if self.wait_error:
            raise self.wait_error
        return self.status


def _mail_fake(runner: Runner) -> FakeMailPoller:
    return cast(FakeMailPoller, runner._mail_pollers[0])


def _github_fake(runner: Runner) -> FakeGitHubPoller:
    return cast(FakeGitHubPoller, runner._github_pollers[0])


def _config(tmp_path: Path, *, max_retries: int = 0) -> Config:
    return Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        print_max_retries=max_retries,
        print_retry_base_delay=0.001,
        print_retry_max_delay=0.001,
    )


def _main_typ(tar: bytes) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        return archive.extractfile("main.typ").read().decode()  # type: ignore[union-attr]


def _runner(
    tmp_path: Path,
    *,
    processor=None,
    max_retries: int = 0,
) -> Runner:
    runner = Runner(
        _config(tmp_path, max_retries=max_retries),
        processor=processor,
    )
    runner._mail_pollers = [FakeMailPoller()]
    runner._github_pollers = [FakeGitHubPoller()]
    return runner


def test_print_succeeded() -> None:
    assert print_succeeded(_status())
    assert not print_succeeded(_status(printer=dzsrv_pb2.PS_NO_PAPER))
    assert not print_succeeded(_status(job=dzsrv_pb2.STATUS_RENDER_FAILED))


def test_success_acks(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    printer = FakePrinter()
    runner.poll(printer)
    assert _mail_fake(runner).seen == ["主题"]
    assert _github_fake(runner).marked
    assert len(printer.tars) == 2
    assert "主题" in _main_typ(printer.tars[0])
    assert "标题" in _main_typ(printer.tars[1])
    assert printer.submit_timeouts == [120.0, 120.0]


@pytest.mark.parametrize(
    "status",
    [
        _status(printer=dzsrv_pb2.PS_NO_PAPER),
        _status(job=dzsrv_pb2.STATUS_RENDER_FAILED),
        _status(job=dzsrv_pb2.STATUS_CANCELLED),
    ],
)
def test_failed_print_is_dead_lettered_after_retries(
    tmp_path: Path,
    status: dzsrv_pb2.JobStatus,
) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(status))
    assert _mail_fake(runner).seen == ["主题"]
    assert _github_fake(runner).marked
    dead = (tmp_path / "dead.jsonl").read_text(encoding="utf-8")
    assert '"kind": "print"' in dead


def test_retry_then_success(tmp_path: Path) -> None:
    class FlakyPrinter(FakePrinter):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def wait(
            self,
            job_id: bytes,
            *,
            timeout: float | None = None,
        ) -> dzsrv_pb2.JobStatus:
            self.calls += 1
            if self.calls == 1:
                return _status(printer=dzsrv_pb2.PS_NO_PAPER)
            return _status()

    runner = _runner(tmp_path, max_retries=2)
    printer = FlakyPrinter()
    runner.poll(printer)
    assert _mail_fake(runner).seen == ["主题"]
    assert _github_fake(runner).marked
    assert not (tmp_path / "dead.jsonl").exists()


def test_wait_error_is_dead_lettered(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(wait_error=TimeoutError("timeout")))
    assert _mail_fake(runner).seen == ["主题"]
    assert _github_fake(runner).marked
    assert '"kind": "print"' in (tmp_path / "dead.jsonl").read_text(encoding="utf-8")


def test_submit_error_is_dead_lettered(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(submit_error=grpc.RpcError()))
    assert _mail_fake(runner).seen == ["主题"]
    assert _github_fake(runner).marked
    assert '"kind": "print"' in (tmp_path / "dead.jsonl").read_text(encoding="utf-8")


def test_processor_error_is_isolated(tmp_path: Path) -> None:
    class BadProcessor:
        def mail(self, item):
            raise RuntimeError("boom")

        def github(self, digest):
            raise RuntimeError("boom")

    runner = _runner(tmp_path, processor=BadProcessor())
    runner.poll(FakePrinter())  # 不应抛出
    assert _mail_fake(runner).seen == []
    assert not _github_fake(runner).marked


def test_mail_poller_uses_max_age(tmp_path: Path) -> None:
    config = Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        mail_max_age=30,
        mail=(MailAccount("me@example.com", "pw", "imap.example.com"),),
    )
    runner = Runner(config)
    poller = cast(MailPoller, runner._mail_pollers[0])
    assert poller.max_age == 30


def test_github_poller_uses_config_interval(tmp_path: Path) -> None:
    config = Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        github_poll_interval=120,
        github=(GitHubAccount("pat"),),
    )
    runner = Runner(config)
    assert runner._github_pollers[0].poll_interval == 120


def test_startup_summary_hides_secrets(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        mail=(
            MailAccount(
                "me@example.com",
                "mail-secret",
                "imap.example.com",
            ),
        ),
        ai=AIConfig(
            enabled=True,
            base_url="https://api.example.com/v1",
            token="sk-secret",
        ),
    )
    runner = Runner(config)

    with caplog.at_level(logging.INFO):
        runner._log_summary()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "me@example.com" in text
    assert "api.example.com" in text
    assert "mail-secret" not in text
    assert "sk-secret" not in text
