"""Usuarios confiables y sesiones autenticadas del núcleo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import uuid
from pathlib import Path
from typing import Callable, Iterable

from access import Role, validate_role
from audit import AuditLogger
from config import load_session_settings

TelegramUserId = int | str
Clock = Callable[[], datetime]


class SessionError(Exception):
    """Error esperado al crear o consultar una sesión."""


def _normalize_telegram_user_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("telegram_user_id debe ser un identificador no vacío.")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("telegram_user_id debe ser un identificador no vacío.")
    return normalized


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    name: str
    role: Role
    student_code: str | None
    telegram_user_id: str | None


class UserRepository:
    def __init__(self, users: Iterable[UserRecord]):
        records = list(users)
        if not records:
            raise ValueError("usuarios.json no contiene usuarios.")
        if len({user.user_id for user in records}) != len(records):
            raise ValueError("usuarios.json contiene ids de usuario duplicados.")
        assigned_ids = [user.telegram_user_id for user in records if user.telegram_user_id is not None]
        if len(set(assigned_ids)) != len(assigned_ids):
            raise ValueError("usuarios.json contiene telegram_user_id duplicados.")
        self._by_telegram_id = {
            user.telegram_user_id: user for user in records if user.telegram_user_id is not None
        }

    @classmethod
    def from_json(cls, path: Path) -> UserRepository:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"No se pudo cargar usuarios.json: {path}") from exc
        raw_users = data.get("usuarios") if isinstance(data, dict) else None
        if not isinstance(raw_users, list):
            raise ValueError("usuarios.json debe contener una lista 'usuarios'.")

        records = []
        seen_ids: set[str] = set()
        for raw in raw_users:
            if not isinstance(raw, dict):
                raise ValueError("Cada usuario debe ser un objeto.")
            user_id = raw.get("id")
            name = raw.get("nombre")
            role = raw.get("rol")
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValueError("Cada usuario requiere un id no vacío.")
            if user_id in seen_ids:
                raise ValueError(f"Usuario duplicado: {user_id!r}.")
            seen_ids.add(user_id)
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Nombre incompleto para {user_id!r}.")
            validated_role = validate_role(role) if isinstance(role, str) else None
            if validated_role is None:
                raise ValueError(f"Rol inválido para {user_id!r}.")
            code = raw.get("codigo_estudiante")
            if validated_role == "estudiante" and (not isinstance(code, str) or not code.strip()):
                raise ValueError(f"El estudiante {user_id!r} requiere codigo_estudiante.")
            if validated_role != "estudiante" and code is not None:
                raise ValueError(f"El usuario no estudiante {user_id!r} no debe tener código.")
            telegram_id = (
                None
                if raw.get("telegram_user_id") is None
                else _normalize_telegram_user_id(raw.get("telegram_user_id"))
            )
            records.append(UserRecord(user_id, name.strip(), validated_role, code, telegram_id))
        return cls(records)

    def resolve(self, telegram_user_id: TelegramUserId) -> UserRecord:
        normalized = _normalize_telegram_user_id(telegram_user_id)
        try:
            return self._by_telegram_id[normalized]
        except KeyError as exc:
            raise SessionError("Telegram ID desconocido.") from exc


@dataclass(frozen=True)
class Session:
    telegram_user_id: str
    name: str
    role: Role
    student_code: str | None
    created_at: datetime
    expires_at: datetime
    session_id: str = ""

    def is_active(self, now: datetime) -> bool:
        return now < self.expires_at


class SessionManager:
    def __init__(
        self,
        users: UserRepository,
        audit: AuditLogger,
        *,
        session_minutes: int | None = None,
        login_max_attempts: int | None = None,
        login_block_minutes: int | None = None,
        clock: Clock | None = None,
    ):
        settings = load_session_settings()
        self.users = users
        self.audit = audit
        self.session_minutes = session_minutes or settings.session_minutes
        self.login_max_attempts = login_max_attempts or settings.login_max_attempts
        self.login_block_minutes = login_block_minutes or settings.login_block_minutes
        if self.session_minutes <= 0 or self.login_max_attempts <= 0 or self.login_block_minutes <= 0:
            raise ValueError("Los parámetros de sesión y bloqueo deben ser mayores que 0.")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._sessions: dict[str, Session] = {}
        self._failed_attempts: dict[str, int] = {}
        self._blocked_until: dict[str, datetime] = {}

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _is_blocked(self, user_id: str, now: datetime, *, reset_attempts: bool = True) -> bool:
        blocked_until = self._blocked_until.get(user_id)
        if blocked_until is None:
            return False
        if now >= blocked_until:
            del self._blocked_until[user_id]
            if reset_attempts:
                self._failed_attempts.pop(user_id, None)
            return False
        return True

    def is_blocked(self, telegram_user_id: TelegramUserId) -> bool:
        """Indica si el usuario mantiene un bloqueo temporal vigente."""
        normalized = _normalize_telegram_user_id(telegram_user_id)
        return self._is_blocked(normalized, self._now(), reset_attempts=False)

    def get_active_session(self, telegram_user_id: TelegramUserId) -> Session | None:
        normalized = _normalize_telegram_user_id(telegram_user_id)
        session = self._sessions.get(normalized)
        if session is None:
            return None
        if not session.is_active(self._now()):
            del self._sessions[normalized]
            return None
        return session

    def create_authenticated_session(
        self, telegram_user_id: TelegramUserId, *, similarity: float | None = None
    ) -> Session:
        normalized = _normalize_telegram_user_id(telegram_user_id)
        now = self._now()
        if self._is_blocked(normalized, now):
            self.audit.record(
                telegram_user_id=telegram_user_id,
                event="login_rechazado",
                reason="cuenta_bloqueada",
                similarity=similarity,
            )
            raise SessionError("La cuenta está bloqueada temporalmente.")
        try:
            user = self.users.resolve(telegram_user_id)
        except SessionError:
            self.audit.record(
                telegram_user_id=telegram_user_id,
                event="login_rechazado",
                reason="telegram_id_desconocido",
                similarity=similarity,
            )
            raise
        session = Session(
            telegram_user_id=normalized,
            name=user.name,
            role=user.role,
            student_code=user.student_code,
            created_at=now,
            expires_at=now + timedelta(minutes=self.session_minutes),
            session_id=str(uuid.uuid4()),
        )
        self._sessions[normalized] = session
        self._failed_attempts.pop(normalized, None)
        self._blocked_until.pop(normalized, None)
        self.audit.record(
            telegram_user_id=telegram_user_id,
            event="login_aceptado",
            similarity=similarity,
        )
        return session

    def record_failed_login(
        self, telegram_user_id: TelegramUserId, *, reason: str, similarity: float | None = None
    ) -> bool:
        normalized = _normalize_telegram_user_id(telegram_user_id)
        now = self._now()
        if self._is_blocked(normalized, now):
            self.audit.record(
                telegram_user_id=telegram_user_id,
                event="login_rechazado",
                reason="cuenta_bloqueada",
                similarity=similarity,
            )
            return False
        attempts = self._failed_attempts.get(normalized, 0) + 1
        self._failed_attempts[normalized] = attempts
        if attempts >= self.login_max_attempts:
            self._blocked_until[normalized] = now + timedelta(minutes=self.login_block_minutes)
            reason = f"{reason};bloqueo_temporal"
        self.audit.record(
            telegram_user_id=telegram_user_id,
            event="login_rechazado",
            reason=reason,
            similarity=similarity,
        )
        return False

    def logout(self, telegram_user_id: TelegramUserId) -> None:
        normalized = _normalize_telegram_user_id(telegram_user_id)
        self._sessions.pop(normalized, None)

    def record_denied_access(
        self, telegram_user_id: TelegramUserId, *, reason: str
    ) -> None:
        self.audit.record(
            telegram_user_id=telegram_user_id,
            event="acceso_denegado",
            reason=reason,
        )

    def record_restricted_tool(
        self, telegram_user_id: TelegramUserId, *, tool: str
    ) -> None:
        self.audit.record(
            telegram_user_id=telegram_user_id,
            event="tool_restringida_ejecutada",
            reason=tool,
        )
