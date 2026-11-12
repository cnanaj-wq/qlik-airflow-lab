"""Modèles de données du lab, indépendants du client HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class ReloadStatus(StrEnum):
    """Statuts d'un reload Qlik Cloud (API /api/v1/reloads)."""

    QUEUED = "QUEUED"
    RELOADING = "RELOADING"
    CANCELING = "CANCELING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    EXCEEDED_LIMIT = "EXCEEDED_LIMIT"

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATUSES

    @property
    def is_success(self) -> bool:
        return self is ReloadStatus.SUCCEEDED


FINAL_STATUSES = frozenset(
    {ReloadStatus.SUCCEEDED, ReloadStatus.FAILED, ReloadStatus.CANCELED, ReloadStatus.EXCEEDED_LIMIT}
)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class ReloadRun:
    """Un reload tel que renvoyé par GET /api/v1/reloads/{id}."""

    id: str
    app_id: str
    status: ReloadStatus
    type: str | None = None
    creation_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def duration_s(self) -> float | None:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ReloadRun:
        return cls(
            id=str(data["id"]),
            app_id=str(data.get("appId", "")),
            status=ReloadStatus(str(data.get("status", "QUEUED"))),
            type=data.get("type"),
            creation_time=_parse_ts(data.get("creationTime")),
            start_time=_parse_ts(data.get("startTime")),
            end_time=_parse_ts(data.get("endTime")),
            error_code=data.get("errorCode") or None,
            error_message=data.get("errorMessage") or None,
        )

    def to_record(self) -> dict[str, Any]:
        """Forme sérialisable (XCom Airflow, supervision)."""
        return {
            "reload_id": self.id,
            "app_id": self.app_id,
            "status": self.status.value,
            "type": self.type,
            "creation_time": self.creation_time.isoformat() if self.creation_time else None,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_s": self.duration_s,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class AppInfo:
    """Une app Qlik Cloud (via /api/v1/items, resourceId = identifiant de l'app)."""

    app_id: str
    name: str
    space_id: str | None = None
