import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_client import MCPClientManager, SESSION_META_KEY
from sessions import Session, UserRepository

USERS_PATH = Path(__file__).parents[1] / "data" / "usuarios.json"


def _session(telegram_id: str) -> Session:
    user = UserRepository.from_json(USERS_PATH).resolve(telegram_id)
    now = datetime.now(timezone.utc)
    return Session(user.telegram_user_id, user.name, user.role, user.student_code, now, now + timedelta(hours=1))


def _manager() -> MCPClientManager:
    key = Ed25519PrivateKey.generate()
    return MCPClientManager(key, key.public_key())


def test_public_tools_and_calendar_are_available_but_private_calls_are_denied() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_public()
            names = {tool.name for tool in (await handle.client.list_tools()).tools}
            assert names == {"fecha_actual", "dias_hasta", "calcular_nota_final"}
            assert not (await manager.call_tool(None, "fecha_actual")).is_error
            assert not (await manager.call_tool(None, "calcular_nota_final", {
                "parcial_1": 4, "parcial_2": 4, "laboratorios": 4, "proyecto": 4,
            })).is_error
            denied = await handle.client.call_tool("consultar_notas", {"codigo": "2026001"})
            assert denied.is_error
            await handle.client.read_resource("academico://calendario")
            try:
                await handle.client.read_resource("academico://privado")
            except Exception:
                pass
            else:
                raise AssertionError("Un recurso desconocido no fue rechazado")
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_student_tools_use_session_code_and_hide_identity_parameters() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_authenticated(_session("6892056328"), session_id="student-1")
            tools = {tool.name: tool for tool in (await handle.client.list_tools()).tools}
            assert {"fecha_actual", "dias_hasta", "calcular_nota_final", "consultar_mis_notas",
                    "consultar_mis_solicitudes", "solicitar_supletorio"} == set(tools)
            assert tools["consultar_mis_notas"].input_schema["properties"] == {}
            assert "codigo" not in tools["solicitar_supletorio"].input_schema["properties"]
            notes = await manager.call_tool("student-1", "consultar_mis_notas")
            assert not notes.is_error
            assert "Jhostyn Santiago" in notes.content[0].text
            denied = await manager.call_tool("student-1", "consultar_notas", {"codigo": "2026001"})
            assert denied.is_error
            write = await manager.call_tool("student-1", "solicitar_supletorio", {"parcial": 1, "causa": "incapacidad médica"})
            assert write.is_error
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_teacher_tools_are_filtered_and_writes_require_confirmation() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_authenticated(_session("9285161265"), session_id="teacher-1")
            names = {tool.name for tool in (await handle.client.list_tools()).tools}
            assert names == {"fecha_actual", "dias_hasta", "calcular_nota_final",
                             "consultar_notas", "consultar_solicitudes", "registrar_nota"}
            assert not (await manager.call_tool("teacher-1", "consultar_notas", {"codigo": "2026001"})).is_error
            denied = await manager.call_tool("teacher-1", "consultar_mis_notas")
            assert denied.is_error
            write = await manager.call_tool("teacher-1", "registrar_nota", {
                "codigo": "2026001", "evaluacion": "parcial_1", "valor": 4.5,
            })
            assert write.is_error
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_invalid_session_metadata_is_rejected_before_tool_execution() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_authenticated(_session("6892056328"), session_id="student-2")
            result = await handle.client.call_tool(
                "consultar_mis_notas",
                {},
                meta={SESSION_META_KEY: "not-a-jwt"},
            )
            assert result.is_error
        finally:
            await manager.close_all()

    asyncio.run(scenario())
