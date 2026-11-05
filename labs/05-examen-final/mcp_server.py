"""Servidor MCP académico con modo público y modo autenticado por proceso."""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from cryptography.hazmat.primitives import serialization
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_identity import (
    IdentityError,
    ProcessIdentityContext,
    load_public_key,
    verify_bootstrap,
    verify_session_token,
)
from sessions import UserRepository

DATA_FILE = Path(__file__).resolve().parent / "data" / "academico.json"
USERS_PATH = Path(os.environ.get("COM_UPB_MCP_USERS_PATH", Path(__file__).resolve().parent / "data" / "usuarios.json"))
DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))
ESTUDIANTES: dict[str, Any] = DATA["estudiantes"]
SOLICITUDES: list[dict[str, Any]] = []
CodigoEstudiante = Annotated[str, Field(description="Código del estudiante, p. ej. 2026001")]


@dataclass(frozen=True)
class AuthenticatedLifespan:
    identity: ProcessIdentityContext
    public_key: Any
    users: UserRepository


def _read_authenticated_bootstrap() -> tuple[ProcessIdentityContext, UserRepository]:
    token = os.environ.pop("COM_UPB_MCP_BOOTSTRAP_TOKEN", None)
    public_key_pem = os.environ.pop("COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY", None)
    if not token or not public_key_pem:
        raise IdentityError("Bootstrap MCP ausente.")
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise IdentityError("Clave pública MCP inválida.") from exc
    users = UserRepository.from_json(USERS_PATH)
    context = verify_bootstrap(token, public_key, users)
    return context, users


def _session_context(ctx: Context) -> ProcessIdentityContext:
    lifespan = ctx.request_context.lifespan_context
    if not isinstance(lifespan, AuthenticatedLifespan):
        raise ToolError("Contexto MCP autenticado ausente.")
    metadata = ctx.request_context.meta
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


def build_server(mode: str, *, public_key=None, users=None) -> MCPServer:
    if mode not in {"public", "authenticated"}:
        raise RuntimeError("Modo MCP inválido.")

    if mode == "authenticated":
        if public_key is None or users is None:
            raise RuntimeError("Contexto autenticado MCP incompleto.")

        @asynccontextmanager
        async def lifespan(server: MCPServer):
            context, _ = _read_authenticated_bootstrap()
            os.environ.pop("COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY", None)
            yield AuthenticatedLifespan(context, public_key, users)
    else:
        @asynccontextmanager
        async def lifespan(server: MCPServer):
            yield None

    server = MCPServer(
        "servicios-academicos",
        instructions="Servicios académicos (ficticios) del curso de Inteligencia Artificial.",
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.resource(
        "academico://calendario",
        description="Fechas clave del curso (parciales y proyecto final)",
    )
    def calendario() -> str:
        return "\n".join(f"{item['fecha']}: {item['evento']}" for item in DATA["calendario"])

    if mode == "authenticated":
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def consultar_notas(codigo: CodigoEstudiante, ctx: Context) -> dict[str, Any]:
            _session_context(ctx)
            estudiante = _buscar_estudiante(codigo)
            notas = estudiante["notas"]
            laboratorios = notas["laboratorios"]
            return {
                "codigo": codigo,
                "nombre": estudiante["nombre"],
                "notas": notas,
                "promedio_laboratorios": round(sum(laboratorios) / len(laboratorios), 2)
                if laboratorios
                else None,
            }

        @server.tool(annotations=ToolAnnotations(readOnly=True))
        def consultar_solicitudes(codigo: CodigoEstudiante, ctx: Context) -> list[dict[str, Any]]:
            _session_context(ctx)
            _buscar_estudiante(codigo)
            return [solicitud for solicitud in SOLICITUDES if solicitud["codigo"] == codigo]

        @server.tool(annotations=ToolAnnotations(readOnly=False, destructiveHint=False))
        def solicitar_supletorio(
            codigo: CodigoEstudiante,
            parcial: Annotated[Literal[1, 2], Field(description="Número del parcial: 1 o 2")],
            causa: Annotated[
                Literal["incapacidad médica", "calamidad doméstica", "representación institucional"],
                Field(description="Causa justificada de la inasistencia"),
            ],
            ctx: Context,
        ) -> dict[str, Any]:
            _session_context(ctx)
            estudiante = _buscar_estudiante(codigo)
            if any(s["codigo"] == codigo and s["parcial"] == parcial for s in SOLICITUDES):
                raise ToolError("Ya existe una solicitud para ese parcial.")
            solicitud = {
                "radicado": f"SUP-2026-{len(SOLICITUDES) + 1:03d}",
                "codigo": codigo,
                "nombre": estudiante["nombre"],
                "parcial": parcial,
                "causa": causa,
                "estado": "pendiente",
            }
            SOLICITUDES.append(solicitud)
            return solicitud

    return server


def _buscar_estudiante(codigo: str) -> dict[str, Any]:
    estudiante = ESTUDIANTES.get(codigo.strip())
    if estudiante is None:
        raise ToolError("No existe el estudiante solicitado.")
    return estudiante


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
        users = UserRepository.from_json(USERS_PATH)
    server = build_server(mode, public_key=public_key, users=users)
    server.run()


if __name__ == "__main__":
    main()
