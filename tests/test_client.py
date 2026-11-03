from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from print_my_mail.client import PrintMyMailClient, print_succeeded
from print_my_mail.proto import dzsrv_pb2, dzsrv_pb2_grpc

JOB_ID = b"1234567890abcdef"


class NotFoundError(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.NOT_FOUND


class FakeClient(PrintMyMailClient):
    def __init__(
        self,
        statuses: Iterable[dzsrv_pb2.JobStatus | Exception],
    ) -> None:
        self._statuses = iter(statuses)

    def status(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
    ) -> dzsrv_pb2.JobStatus:
        item = next(self._statuses)
        if isinstance(item, Exception):
            raise item
        return item


def test_wait_polls_until_succeeded() -> None:
    client = FakeClient(
        [
            dzsrv_pb2.JobStatus(state=dzsrv_pb2.JOB_STATE_UNSPECIFIED),
            dzsrv_pb2.JobStatus(state=dzsrv_pb2.JOB_STATE_QUEUED),
            dzsrv_pb2.JobStatus(state=dzsrv_pb2.JOB_STATE_RENDERING),
            dzsrv_pb2.JobStatus(
                state=dzsrv_pb2.JOB_STATE_SUCCEEDED,
                printer_status=dzsrv_pb2.PS_NO_JOB,
            ),
        ]
    )

    status = client.wait(b"job", timeout=1, poll_interval=0)

    assert status.state == dzsrv_pb2.JOB_STATE_SUCCEEDED
    assert print_succeeded(status)


def test_wait_returns_failed_terminal() -> None:
    failed = dzsrv_pb2.JobStatus(state=dzsrv_pb2.JOB_STATE_FAILED)
    client = FakeClient([failed])

    assert client.wait(b"job", poll_interval=0).state == (
        dzsrv_pb2.JOB_STATE_FAILED
    )


def test_wait_converts_not_found_to_lookup_error() -> None:
    client = FakeClient([NotFoundError()])

    with pytest.raises(LookupError):
        client.wait(b"job", poll_interval=0)


def test_cancel_returns_accepted() -> None:
    class Mgmt:
        def Cancel(self, request: object, timeout: float | None = None):
            return dzsrv_pb2.CancelReply(
                accepted=True,
                state=dzsrv_pb2.JOB_STATE_QUEUED,
            )

    client = FakeClient([])
    client._mgmt = Mgmt()  # type: ignore[assignment]

    assert client.cancel(b"job")


def test_client_round_trip_with_new_proto() -> None:
    class PrintServicer(dzsrv_pb2_grpc.PrintServicer):
        def PrintImage(
            self,
            request: dzsrv_pb2.PrintImageReq,
            context: grpc.ServicerContext,
        ) -> dzsrv_pb2.JobHandle:
            return dzsrv_pb2.JobHandle(job_id=JOB_ID)

        def PrintTypst(
            self,
            request: dzsrv_pb2.PrintTypstReq,
            context: grpc.ServicerContext,
        ) -> dzsrv_pb2.JobHandle:
            assert request.typst_tar == b"tar"
            return dzsrv_pb2.JobHandle(job_id=JOB_ID)

    class MgmtServicer(dzsrv_pb2_grpc.MgmtServicer):
        def __init__(self) -> None:
            self.status_calls = 0

        def Status(
            self,
            request: dzsrv_pb2.JobHandle,
            context: grpc.ServicerContext,
        ) -> dzsrv_pb2.JobStatus:
            assert request.job_id == JOB_ID
            self.status_calls += 1
            if self.status_calls == 1:
                return dzsrv_pb2.JobStatus(
                    state=dzsrv_pb2.JOB_STATE_QUEUED
                )
            return dzsrv_pb2.JobStatus(
                state=dzsrv_pb2.JOB_STATE_SUCCEEDED,
                printer_status=dzsrv_pb2.PS_NO_JOB,
            )

        def Cancel(
            self,
            request: dzsrv_pb2.JobHandle,
            context: grpc.ServicerContext,
        ) -> dzsrv_pb2.CancelReply:
            return dzsrv_pb2.CancelReply(
                accepted=True,
                state=dzsrv_pb2.JOB_STATE_QUEUED,
            )

    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    dzsrv_pb2_grpc.add_PrintServicer_to_server(PrintServicer(), server)
    dzsrv_pb2_grpc.add_MgmtServicer_to_server(MgmtServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with PrintMyMailClient(f"127.0.0.1:{port}") as client:
            job_id = client.print_typst(b"tar", timeout=5)
            assert job_id == JOB_ID

            status = client.wait(job_id, timeout=5, poll_interval=0)
            assert status.state == dzsrv_pb2.JOB_STATE_SUCCEEDED
            assert print_succeeded(status)

            assert client.cancel(job_id, timeout=5)
    finally:
        server.stop(0)
