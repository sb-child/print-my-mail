"""从 proto/dzsrv.proto 生成 gRPC Python 代码。

用法：uv run python scripts/gen_proto.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "src" / "print_my_mail" / "proto"

PROTO_FILES = ["dzsrv.proto"]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    args = [
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        _plugin_arg("protoc-gen-mypy"),
        _plugin_arg("protoc-gen-mypy_grpc"),
        f"--mypy_out={OUT_DIR}",
        f"--mypy_grpc_out={OUT_DIR}",
        *(str(PROTO_DIR / name) for name in PROTO_FILES),
    ]
    code = protoc.main(args)
    if code != 0:
        raise SystemExit(f"protoc 失败，退出码 {code}")

    # grpc_tools / mypy-protobuf 生成的是绝对导入（import dzsrv_pb2），
    # 放进包内后改为相对导入。
    for proto in PROTO_FILES:
        stem = Path(proto).stem
        for suffix in ("_pb2_grpc.py", "_pb2_grpc.pyi"):
            generated = OUT_DIR / f"{stem}{suffix}"
            if not generated.exists():
                continue
            text = generated.read_text(encoding="utf-8")
            text = text.replace(f"import {stem}_pb2 as ", f"from . import {stem}_pb2 as ")
            generated.write_text(text, encoding="utf-8")


def _plugin_arg(name: str) -> str:
    """找到 mypy-protobuf 提供的 protoc 插件，并生成 --plugin 参数。"""
    path = shutil.which(name) or str(Path(sys.executable).with_name(name))
    return f"--plugin={name}={path}"


if __name__ == "__main__":
    main()
