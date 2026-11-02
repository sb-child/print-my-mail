from __future__ import annotations

import io
import tarfile
from datetime import datetime
from pathlib import Path

import grpc
import pytest

from print_my_mail import (
    Config,
    GitHubDigest,
    GitHubItem,
    GitHubNotification,
    MailItem,
)
from print_my_mail.client import print_succeeded
from print_my_mail.proto import dzsrv_pb2
from print_my_mail.runner import Runner


def _status(
    job: int = dzsrv_pb2.STATUS_PRINT_COMPLETED,
    printer: int = dzsrv_pb2.PS_PRINTABLE,
) -> dzsrv_pb2.JobStatus:
    return dzsrv_pb2.JobStatus(
        exists=True,
        job_status=job,
        printer_status=printer,
    )


class FakeMailPoller:
    def __init__(self) -> None:
        self.account = type("Account", (), {"user": "me@example.com"})()
        self.seen: list[str] = []

    def poll(self):
        yield MailItem(
            "me@example.com",
            "sender@example.com",
            "发件人",
            "主题",
            datetime.now(),
            "正文",
        )
        self.seen.append("主题")


class FakeGitHubPoller:
    def __init__(self) -> None:
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

    def print_typst(self, tar: bytes) -> bytes:
        if self.submit_error:
            raise self.submit_error
        self.tars.append(tar)
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


def _config(tmp_path: Path) -> Config:
    return Config(dead_letter_file=str(tmp_path / "dead.jsonl"))


def _main_typ(tar: bytes) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        return archive.extractfile("main.typ").read().decode()  # type: ignore[union-attr]


def _runner(tmp_path: Path, *, processor=None) -> Runner:
    runner = Runner(_config(tmp_path), processor=processor)
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
    assert runner._mail_pollers[0].seen == ["主题"]
    assert runner._github_pollers[0].marked
    assert len(printer.tars) == 2
    assert "主题" in _main_typ(printer.tars[0])
    assert "标题" in _main_typ(printer.tars[1])


@pytest.mark.parametrize(
    "status",
    [
        _status(printer=dzsrv_pb2.PS_NO_PAPER),
        _status(job=dzsrv_pb2.STATUS_RENDER_FAILED),
        _status(job=dzsrv_pb2.STATUS_CANCELLED),
    ],
)
def test_failed_print_does_not_ack(
    tmp_path: Path,
    status: dzsrv_pb2.JobStatus,
) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(status))
    assert runner._mail_pollers[0].seen == []
    assert not runner._github_pollers[0].marked


def test_wait_error_does_not_ack(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(wait_error=TimeoutError("timeout")))
    assert runner._mail_pollers[0].seen == []
    assert not runner._github_pollers[0].marked


def test_submit_error_does_not_ack(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.poll(FakePrinter(submit_error=grpc.RpcError()))
    assert runner._mail_pollers[0].seen == []
    assert not runner._github_pollers[0].marked


def test_processor_error_is_isolated(tmp_path: Path) -> None:
    class BadProcessor:
        def mail(self, item):
            raise RuntimeError("boom")

        def github(self, digest):
            raise RuntimeError("boom")

    runner = _runner(tmp_path, processor=BadProcessor())
    runner.poll(FakePrinter())  # 不应抛出
    assert runner._mail_pollers[0].seen == []
    assert not runner._github_pollers[0].marked
