"""Políticas deterministas de acceso a documentos y capacidades MCP."""

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Final, Literal, Mapping, Protocol, cast

from tools import LOCAL_TOOLS

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


@dataclass(frozen=True)
class ToolPolicy:
    """Autorización declarativa de una herramienta MCP."""

    roles: frozenset[Role]
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ResourcePolicy:
    """Autorización declarativa de un recurso MCP."""

    roles: frozenset[Role]


LOCAL_PUBLIC_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    tool.name for tool in LOCAL_TOOLS if tool.origin == "local"
)

TOOL_POLICIES: Final[dict[str, ToolPolicy]] = {
    **{
        name: ToolPolicy(frozenset({"publico", "estudiante", "docente"}))
        for name in LOCAL_PUBLIC_TOOL_NAMES
    },
    "consultar_mis_notas": ToolPolicy(frozenset({"estudiante"})),
    "consultar_mis_solicitudes": ToolPolicy(frozenset({"estudiante"})),
    "solicitar_supletorio": ToolPolicy(frozenset({"estudiante"}), requires_confirmation=True),
    "consultar_notas": ToolPolicy(frozenset({"docente"})),
    "consultar_solicitudes": ToolPolicy(frozenset({"docente"})),
    "registrar_nota": ToolPolicy(frozenset({"docente"}), requires_confirmation=True),
}

RESOURCE_POLICIES: Final[dict[str, ResourcePolicy]] = {
    "academico://calendario": ResourcePolicy(frozenset({"publico", "estudiante", "docente"})),
}


def tool_policy(tool_name: str) -> ToolPolicy | None:
    """Devuelve la política de una tool, o ``None`` si no está registrada."""
    return TOOL_POLICIES.get(tool_name)


def allowed_tools(role: str) -> frozenset[str]:
    """Devuelve las tools autorizadas para un rol reconocido."""
    if role not in ROLES:
        return frozenset()
    validated_role = validate_role(role)
    return frozenset(
        name for name, policy in TOOL_POLICIES.items() if validated_role in policy.roles
    )


def authorize_tool(role: str, tool_name: str, arguments: Mapping[str, object] | None = None) -> bool:
    """Comprueba una autorización MCP sin validar todavía identidad o JWT."""
    if role not in ROLES:
        return False
    policy = tool_policy(tool_name)
    if policy is None or validate_role(role) not in policy.roles:
        return False
    if tool_name in {"consultar_mis_notas", "consultar_mis_solicitudes", "solicitar_supletorio"}:
        return not arguments or "codigo" not in arguments
    return True


def requires_confirmation(tool_name: str) -> bool:
    """Indica si una tool requiere confirmación explícita."""
    policy = tool_policy(tool_name)
    return policy is not None and policy.requires_confirmation


def resource_policy(resource_uri: str) -> ResourcePolicy | None:
    """Devuelve la política del recurso MCP, separado de las tools."""
    return RESOURCE_POLICIES.get(resource_uri)


def authorize_resource(role: str, resource_uri: str) -> bool:
    """Comprueba el acceso a un recurso MCP reconocido."""
    if role not in ROLES:
        return False
    policy = resource_policy(resource_uri)
    return policy is not None and validate_role(role) in policy.roles


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
