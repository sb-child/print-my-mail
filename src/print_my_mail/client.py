"""dzsrv 的 gRPC 客户端封装。"""

from __future__ import annotations

import time
from pathlib import Path

import grpc

from .proto import dzsrv_pb2, dzsrv_pb2_grpc

DEFAULT_TARGET = "127.0.0.1:54321"

# 这三个状态不会再有后续变化。
TERMINAL_STATUSES = frozenset(
    {
        dzsrv_pb2.STATUS_RENDER_FAILED,
        dzsrv_pb2.STATUS_PRINT_COMPLETED,
        dzsrv_pb2.STATUS_CANCELLED,
    }
)


def print_succeeded(status: dzsrv_pb2.JobStatus) -> bool:
    """判断一个终态任务是否真的打印成功。

    dz-server 在成功时把 printer_status 设为 PS_PRINTABLE；
    STATUS_PRINT_COMPLETED 也可能表示打印失败，必须结合 printer_status 判断。
    """
    return (
        status.job_status == dzsrv_pb2.STATUS_PRINT_COMPLETED
        and status.printer_status == dzsrv_pb2.PS_PRINTABLE
    )


class PrintMyMailClient:
    """print-my-mail 服务端客户端。

    默认连接明文 gRPC（`grpc.insecure_channel`）。也可以传入已有的
    `grpc.Channel`（例如带 TLS 或拦截器的 channel）：

        channel = grpc.secure_channel(target, credentials)
        client = PrintMyMailClient(channel=channel)
    """

    def __init__(
        self,
        target: str = DEFAULT_TARGET,
        *,
        channel: grpc.Channel | None = None,
    ) -> None:
        self._owns_channel = channel is None
        self._channel = (
            channel if channel is not None else grpc.insecure_channel(target)
        )
        self._print = dzsrv_pb2_grpc.PrintStub(self._channel)
        self._mgmt = dzsrv_pb2_grpc.MgmtStub(self._channel)

    @property
    def channel(self) -> grpc.Channel:
        return self._channel

    def print_image(
        self,
        image: bytes | str | Path,
        repeats: int = 0,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """提交一张 png/jpg 图片，返回 16 字节的 job_id。

        `repeats` 为 0 表示打印一次，1 表示打印两次，以此类推。
        """
        request = dzsrv_pb2.PrintImageReq(
            image_file=_as_bytes(image),
            repeats=repeats,
        )
        response = self._print.PrintImage(request, timeout=timeout)
        return response.job_id

    def print_typst(
        self,
        typst_tar: bytes | str | Path,
        repeats: int = 0,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """提交一个包含 main.typ 的 tar 包，返回 16 字节的 job_id。"""
        request = dzsrv_pb2.PrintTypstReq(
            typst_tar=_as_bytes(typst_tar),
            repeats=repeats,
        )
        response = self._print.PrintTypst(request, timeout=timeout)
        return response.job_id

    def status(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
    ) -> dzsrv_pb2.JobStatus:
        """查询任务状态。"""
        response = self._mgmt.Status(
            dzsrv_pb2.JobHandle(job_id=job_id),
            timeout=timeout,
        )
        return response

    def cancel(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
    ) -> bool:
        """取消任务，返回是否取消成功。"""
        response = self._mgmt.Cancel(
            dzsrv_pb2.JobHandle(job_id=job_id),
            timeout=timeout,
        )
        return response.cancelled

    def wait(
        self,
        job_id: bytes,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        rpc_timeout: float | None = None,
    ) -> dzsrv_pb2.JobStatus:
        """轮询直到任务进入终态，并返回最终状态。

        `timeout` 是整体等待时间；`rpc_timeout` 是单次 Status 调用的超时。
        超时将抛出 `TimeoutError`，任务不存在将抛出 `LookupError`。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is None:
                call_timeout = rpc_timeout
            else:
                remaining = max(deadline - time.monotonic(), 0.0)
                call_timeout = (
                    remaining if rpc_timeout is None else min(rpc_timeout, remaining)
                )

            status = self.status(job_id, timeout=call_timeout)
            if not status.exists:
                raise LookupError(f"任务不存在: {job_id.hex()}")
            if status.job_status in TERMINAL_STATUSES:
                return status

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"等待任务 {job_id.hex()} 超时")
            time.sleep(poll_interval)

    def close(self) -> None:
        if self._owns_channel:
            self._channel.close()

    def __enter__(self) -> PrintMyMailClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _as_bytes(data: bytes | str | Path) -> bytes:
    if isinstance(data, bytes):
        return data
    return Path(data).read_bytes()
