"""Servidor MCP académico con autorización efectiva por rol."""

from __future__ import annotations

import json
import os
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.hazmat.primitives import serialization
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import (
    ListToolsResult,
    ReadResourceResult,
    ToolAnnotations,
)
from pydantic import Field

from access import (
    allowed_tools,
    authorize_resource,
    authorize_tool,
    requires_confirmation,
)
from audit import AuditLogger
from mcp_identity import (
    IdentityError,
    ProcessIdentityContext,
    verify_bootstrap,
    verify_confirmation_token,
    verify_session_token,
)
from sessions import UserRepository
from tools import calcular_nota_final, dias_hasta, fecha_actual

DATA_FILE = Path(__file__).resolve().parent / "data" / "academico.json"
DEFAULT_USERS_PATH = Path(__file__).resolve().parent / "data" / "usuarios.json"
AUDIT_PATH = Path(os.environ.get("COM_UPB_MCP_AUDIT_PATH", Path(__file__).resolve().parent / "data" / "auditoria_mcp.jsonl"))
DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))
ESTUDIANTES: dict[str, Any] = DATA["estudiantes"]
SOLICITUDES: list[dict[str, Any]] = []
CodigoEstudiante = Annotated[str, Field(description="Código del estudiante, p. ej. 2026001")]


@dataclass(frozen=True)
class AuthenticatedLifespan:
    identity: ProcessIdentityContext
    public_key: Any
    users: UserRepository
    consumed_confirmations: set[str]
    confirmation_lock: asyncio.Lock


def _read_authenticated_bootstrap(users_path: Path) -> tuple[ProcessIdentityContext, UserRepository, Any]:
    token = os.environ.pop("COM_UPB_MCP_BOOTSTRAP_TOKEN", None)
    public_key_pem = os.environ.pop("COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY", None)
    if not token or not public_key_pem:
        raise IdentityError("Bootstrap MCP ausente.")
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise IdentityError("Clave pública MCP inválida.") from exc
    users = UserRepository.from_json(users_path)
    return verify_bootstrap(token, public_key, users), users, public_key


def _identity_from_context(context: Context | None) -> ProcessIdentityContext | None:
    lifespan = context.request_context.lifespan_context if context and context.request_context else None
    return lifespan.identity if isinstance(lifespan, AuthenticatedLifespan) else None


def _role_from_context(context: Context | None) -> str:
    identity = _identity_from_context(context)
    return identity.role if identity else "publico"


def _audit_denial(context: Context | None, reason: str) -> None:
    _audit_event(context, "mcp_autorizacion_denegada", reason)


def _audit_event(context: Context | None, event: str, reason: str | None = None) -> None:
    identity = _identity_from_context(context)
    AuditLogger(AUDIT_PATH).record(
        telegram_user_id=identity.telegram_user_id if identity else None,
        event=event,
        reason=reason,
    )


def _session_context(context: Context) -> ProcessIdentityContext:
    lifespan = context.request_context.lifespan_context
    if not isinstance(lifespan, AuthenticatedLifespan):
        raise ToolError("Contexto MCP autenticado ausente.")
    metadata = context.request_context.meta
    token = metadata.get("com.upb.exam/session-token") if isinstance(metadata, dict) else None
    if not isinstance(token, str):
        raise ToolError("Token de sesión MCP ausente.")
    try:
        return verify_session_token(
            token,
            lifespan.public_key,
            lifespan.users,
            expected_context=lifespan.identity,
        )
    except IdentityError as exc:
        raise ToolError("Token de sesión MCP inválido.") from exc


class AuthorizedMCPServer(MCPServer):
    async def _handle_list_tools(self, ctx, params):
        result: ListToolsResult = await super()._handle_list_tools(ctx, params)
        role = _role_from_context(Context(request_context=ctx, mcp_server=self))
        permitted = allowed_tools(role)
        return ListToolsResult(tools=[tool for tool in result.tools if tool.name in permitted])

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context | None = None):
        role = _role_from_context(context)
        if not authorize_tool(role, name, arguments):
            _audit_denial(context, f"tool_no_autorizada:{name}")
            raise ToolError("Herramienta no autorizada para el rol autenticado.")
        if requires_confirmation(name):
            lifespan = context.request_context.lifespan_context if context else None
            metadata = context.request_context.meta if context and context.request_context else None
            confirmation = metadata.get("com.upb.exam/confirmation-token") if isinstance(metadata, dict) else None
            if not isinstance(lifespan, AuthenticatedLifespan) or not isinstance(confirmation, str):
                _audit_denial(context, f"confirmacion_ausente:{name}")
                raise ToolError("La operación requiere un comprobante de confirmación válido.")
            try:
                confirmation_jti = verify_confirmation_token(
                    confirmation,
                    lifespan.public_key,
                    expected_context=lifespan.identity,
                    tool_name=name,
                    arguments=arguments,
                )
            except IdentityError as exc:
                _audit_denial(context, f"confirmacion_invalida:{name}")
                raise ToolError("Comprobante de confirmación inválido.") from exc
            async with lifespan.confirmation_lock:
                if confirmation_jti in lifespan.consumed_confirmations:
                    _audit_denial(context, f"confirmacion_reutilizada:{name}")
                    raise ToolError("El comprobante de confirmación ya fue utilizado.")
                lifespan.consumed_confirmations.add(confirmation_jti)
            _audit_event(context, "mcp_confirmacion_consumida", name)
        if role != "publico":
            _session_context(context)
        result = await super().call_tool(name, arguments, context)
        if requires_confirmation(name):
            _audit_event(context, "mcp_escritura_resultado", "error" if result.is_error else "ok")
        return result

    async def read_resource(self, uri, context=None):
        role = _role_from_context(context)
        if not authorize_resource(role, str(uri)):
            _audit_denial(context, f"recurso_no_autorizado:{uri}")
            raise ResourceError("Recurso MCP no autorizado.")
        if role != "publico":
            _session_context(context)
        return await super().read_resource(uri, context)


def build_server(mode: str, *, public_key=None, users=None, users_path: Path = DEFAULT_USERS_PATH) -> MCPServer:
    if mode not in {"public", "authenticated"}:
        raise RuntimeError("Modo MCP inválido.")
    if mode == "authenticated" and (public_key is None or users is None):
        raise RuntimeError("Contexto autenticado MCP incompleto.")

    @asynccontextmanager
    async def lifespan(server: MCPServer):
        if mode == "authenticated":
            identity, repository, verified_key = _read_authenticated_bootstrap(users_path)
            yield AuthenticatedLifespan(identity, verified_key, repository, set(), asyncio.Lock())
        else:
            yield None

    server = AuthorizedMCPServer(
        "servicios-academicos",
        instructions="Servicios académicos (ficticios) del curso de Inteligencia Artificial.",
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.tool(name="fecha_actual", annotations=ToolAnnotations(readOnlyHint=True))
    def fecha_actual_mcp() -> dict[str, str]:
        return fecha_actual()

    @server.tool(name="dias_hasta", annotations=ToolAnnotations(readOnlyHint=True))
    def dias_hasta_mcp(fecha: Annotated[str, Field(description="Fecha en formato AAAA-MM-DD")]) -> dict[str, Any]:
        return dias_hasta(fecha)

    @server.tool(name="calcular_nota_final", annotations=ToolAnnotations(readOnlyHint=True))
    def calcular_nota_final_mcp(
        parcial_1: Annotated[float, Field(ge=0, le=5)],
        parcial_2: Annotated[float, Field(ge=0, le=5)],
        laboratorios: Annotated[float, Field(ge=0, le=5)],
        proyecto: Annotated[float, Field(ge=0, le=5)],
    ) -> dict[str, Any]:
        return calcular_nota_final(parcial_1, parcial_2, laboratorios, proyecto)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def consultar_mis_notas(ctx: Context) -> dict[str, Any]:
        identity = _session_context(ctx)
        return _notas(identity.codigo)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def consultar_mis_solicitudes(ctx: Context) -> list[dict[str, Any]]:
        identity = _session_context(ctx)
        return [item for item in SOLICITUDES if item["codigo"] == identity.codigo]

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    def solicitar_supletorio(
        parcial: Annotated[Literal[1, 2], Field(description="Número del parcial: 1 o 2")],
        causa: Annotated[
            Literal["incapacidad médica", "calamidad doméstica", "representación institucional"],
            Field(description="Causa justificada"),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        identity = _session_context(ctx)
        if not identity.codigo:
            raise ToolError("El contexto no tiene código académico.")
        estudiante = _buscar_estudiante(identity.codigo)
        if any(s["codigo"] == identity.codigo and s["parcial"] == parcial for s in SOLICITUDES):
            raise ToolError("Ya existe una solicitud para ese parcial.")
        solicitud = {
            "radicado": f"SUP-2026-{len(SOLICITUDES) + 1:03d}",
            "codigo": identity.codigo,
            "nombre": estudiante["nombre"],
            "parcial": parcial,
            "causa": causa,
            "estado": "pendiente",
        }
        SOLICITUDES.append(solicitud)
        return solicitud

    @server.tool(annotations=ToolAnnotations(readOnly=True))
    def consultar_notas(codigo: CodigoEstudiante, ctx: Context) -> dict[str, Any]:
        _session_context(ctx)
        return _notas(codigo)

    @server.tool(annotations=ToolAnnotations(readOnly=True))
    def consultar_solicitudes(codigo: CodigoEstudiante, ctx: Context) -> list[dict[str, Any]]:
        _session_context(ctx)
        _buscar_estudiante(codigo)
        return [item for item in SOLICITUDES if item["codigo"] == codigo]

    @server.tool(annotations=ToolAnnotations(readOnly=False, destructiveHint=False))
    def registrar_nota(
        codigo: CodigoEstudiante,
        evaluacion: Annotated[str, Field(min_length=1)],
        valor: Annotated[float, Field(ge=0, le=5)],
        ctx: Context,
    ) -> dict[str, Any]:
        _session_context(ctx)
        estudiante = _buscar_estudiante(codigo)
        if evaluacion not in {"parcial_1", "parcial_2", "proyecto"}:
            raise ToolError("Evaluación no válida.")
        estudiante["notas"][evaluacion] = valor
        return {"codigo": codigo, "evaluacion": evaluacion, "valor": valor}

    @server.resource("academico://calendario", description="Fechas clave del curso")
    def calendario() -> str:
        return "\n".join(f"{item['fecha']}: {item['evento']}" for item in DATA["calendario"])

    return server


def _buscar_estudiante(codigo: str) -> dict[str, Any]:
    normalized = codigo.strip()
    estudiante = ESTUDIANTES.get(normalized) or ESTUDIANTES.get(f"<{normalized}>")
    if estudiante is None:
        raise ToolError("No existe el estudiante solicitado.")
    return estudiante


def _notas(codigo: str | None) -> dict[str, Any]:
    if not codigo:
        raise ToolError("El contexto no tiene código académico.")
    estudiante = _buscar_estudiante(codigo)
    notas = estudiante["notas"]
    laboratorios = notas["laboratorios"]
    return {
        "codigo": codigo,
        "nombre": estudiante["nombre"],
        "notas": notas,
        "promedio_laboratorios": round(sum(laboratorios) / len(laboratorios), 2) if laboratorios else None,
    }


def main() -> None:
    mode = os.environ.get("COM_UPB_MCP_MODE")
    if mode not in {"public", "authenticated"}:
        raise RuntimeError("COM_UPB_MCP_MODE es obligatorio.")
    public_key = None
    users = None
    if mode == "authenticated":
        raw_key = os.environ.get("COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY")
        if not raw_key:
            raise RuntimeError("Clave pública MCP ausente.")
        public_key = serialization.load_pem_public_key(raw_key.encode("ascii"))
        users = UserRepository.from_json(Path(os.environ.get("COM_UPB_MCP_USERS_PATH", DEFAULT_USERS_PATH)))
    server = build_server(mode, public_key=public_key, users=users)
    server.run()


if __name__ == "__main__":
    main()
