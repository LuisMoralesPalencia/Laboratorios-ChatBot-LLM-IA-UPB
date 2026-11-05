"""Auditoría JSONL de eventos de seguridad sin datos biométricos."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: Path):
        self.path = path

    def record(
        self,
        *,
        telegram_user_id: int | str | None,
        event: str,
        reason: str | None = None,
        similarity: float | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "telegram_user_id": telegram_user_id,
            "event": event,
        }
        if reason is not None:
            entry["reason"] = reason
        if similarity is not None:
            entry["similarity"] = similarity
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
