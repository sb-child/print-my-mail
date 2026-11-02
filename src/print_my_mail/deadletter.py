"""无法处理的通知死信记录。

每条 JSONL 包含：time（UTC+8）、kind、account、item、reason、details。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from .timeutil import now

log = logging.getLogger(__name__)


class DeadLetterStore:
    """把永久失败的通知追加写入 JSONL 文件。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(
        self,
        *,
        kind: str,
        account: str,
        item: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "time": now().isoformat(timespec="seconds"),
            "kind": kind,
            "account": account,
            "item": item,
            "reason": reason,
            "details": details or {},
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.error("写入死信文件 %s 失败: %s", self.path, exc)
