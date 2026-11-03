"""dzsrv 的 gRPC 客户端封装。

对应 dz-print 新版 proto：`JobStatus.state` 表示任务状态，失败详情在
`JobStatus.failure`，成功只看 `JOB_STATE_SUCCEEDED`。

用法::

    from print_my_mail import PrintMyMailClient
    from print_my_mail.template import make_mail_tar
    from print_my_mail.timeutil import now

    tar = make_mail_tar(
        recp="me@example.com",
        sender="sender@example.com",
        t=now(),
        title="标题",
        sender_name="发件人",
        content="正文",
    )
    with PrintMyMailClient("127.0.0.1:54321") as client:
        job_id = client.print_typst(tar, timeout=30)
        status = client.wait(job_id, timeout=120)
        print(status.state, status.printer_status)

默认连接明文 gRPC；也可以传入已有的 `grpc.Channel`（例如带 TLS 或
拦截器的 channel）::

    import grpc

    channel = grpc.secure_channel(target, credentials)
    client = PrintMyMailClient(channel=channel)

打印服务由 dz-print 提供，启动示例::

    cargo r -r --bin dzserver /tmp/dzserver 127.0.0.1:54321 \\
        --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Light.ttf \\
        --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Medium.ttf \\
        --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Regular.ttf
"""

from __future__ import annotations

import time
from pathlib import Path

import grpc

from .proto import dzsrv_pb2, dzsrv_pb2_grpc

DEFAULT_TARGET = "127.0.0.1:54321"

# 这三个任务状态不会再有后续变化。
TERMINAL_STATES = frozenset(
    {
        dzsrv_pb2.JOB_STATE_SUCCEEDED,
        dzsrv_pb2.JOB_STATE_FAILED,
        dzsrv_pb2.JOB_STATE_CANCELLED,
    }
)


def print_succeeded(status: dzsrv_pb2.JobStatus) -> bool:
    """判断一个终态任务是否打印成功。

    新协议里成功只由 `state == JOB_STATE_SUCCEEDED` 决定；
    `printer_status` 仅供展示和排障，不参与成败判定
    （成功时它也可能是 `PS_NO_JOB`）。
    """
    return status.state == dzsrv_pb2.JOB_STATE_SUCCEEDED


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
        """请求取消任务，返回服务端是否接受。

        取消是异步的；接受后任务会进入 `JOB_STATE_CANCELLED`。
        """
        response = self._mgmt.Cancel(
            dzsrv_pb2.JobHandle(job_id=job_id),
            timeout=timeout,
        )
        return response.accepted

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
        超时将抛出 `TimeoutError`；任务不存在或已被回收（`NOT_FOUND`）
        将抛出 `LookupError`。
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

            try:
                status = self.status(job_id, timeout=call_timeout)
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    raise LookupError(
                        f"任务不存在: {job_id.hex()}"
                    ) from exc
                raise
            if status.state in TERMINAL_STATES:
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
