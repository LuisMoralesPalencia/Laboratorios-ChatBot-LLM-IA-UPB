"""Políticas deterministas de acceso a documentos del curso."""

from datetime import datetime, timezone
from typing import Final, Literal, Protocol, cast

Role = Literal["publico", "estudiante", "docente"]
AccessLevel = Literal["publico", "estudiante", "docente"]


class ActiveSession(Protocol):
    role: Role
    expires_at: datetime

ROLES: Final[frozenset[str]] = frozenset({"publico", "estudiante", "docente"})
ACCESS_LEVELS_BY_ROLE: Final[dict[Role, frozenset[AccessLevel]]] = {
    "publico": frozenset({"publico"}),
    "estudiante": frozenset({"publico", "estudiante"}),
    "docente": frozenset({"publico", "estudiante", "docente"}),
}


def validate_role(role: str) -> Role:
    if role not in ROLES:
        raise ValueError(f"Rol desconocido: {role!r}")
    return cast(Role, role)


def allowed_levels(role: str) -> frozenset[AccessLevel]:
    return ACCESS_LEVELS_BY_ROLE[validate_role(role)]


def can_access(role: str, access_level: str) -> bool:
    if access_level not in {"publico", "estudiante", "docente"}:
        raise ValueError(f"Nivel de acceso desconocido: {access_level!r}")
    return access_level in allowed_levels(role)


def effective_role(session: ActiveSession | None, now: datetime | None = None) -> Role:
    """Devuelve el rol efectivo; una sesión ausente o vencida solo es pública."""
    if session is None:
        return "publico"
    current_time = now or datetime.now(timezone.utc)
    if session.expires_at <= current_time:
        return "publico"
    return validate_role(session.role)
