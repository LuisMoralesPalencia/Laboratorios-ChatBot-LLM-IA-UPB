import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_client import MCPClientManager, ProcessState, public_server_parameters
from sessions import Session, UserRepository

USERS_PATH = Path(__file__).parents[1] / "data" / "usuarios.json"


def _session() -> Session:
    user = UserRepository.from_json(USERS_PATH).resolve("6892056328")
    now = datetime.now(timezone.utc)
    return Session(user.telegram_user_id, user.name, user.role, user.student_code, now, now + timedelta(hours=1))


def test_public_process_only_exposes_resource_and_isolated_from_authenticated() -> None:
    async def scenario() -> None:
        private_key = Ed25519PrivateKey.generate()
        manager = MCPClientManager(private_key, private_key.public_key())
        try:
            public = await manager.get_or_create_public()
            authenticated = await manager.get_or_create_authenticated(_session(), session_id="session-1")
            assert public is not authenticated
            assert (await public.client.list_tools()).tools == []
            assert len((await public.client.read_resource("academico://calendario")).contents) == 1
            assert {tool.name for tool in (await authenticated.client.list_tools()).tools} == {
                "consultar_notas",
                "consultar_solicitudes",
                "solicitar_supletorio",
            }
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_authenticated_call_transports_session_token_in_meta() -> None:
    async def scenario() -> None:
        private_key = Ed25519PrivateKey.generate()
        manager = MCPClientManager(private_key, private_key.public_key())
        try:
            await manager.get_or_create_authenticated(_session(), session_id="session-1")
            result = await manager.call_tool("session-1", "consultar_notas", {"codigo": "<000550926>"})
            assert result.is_error is False
            assert "Jhostyn Santiago" in result.content[0].text
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_concurrent_requests_reuse_one_authenticated_process() -> None:
    async def scenario() -> None:
        private_key = Ed25519PrivateKey.generate()
        manager = MCPClientManager(private_key, private_key.public_key())
        try:
            handles = await asyncio.gather(
                manager.get_or_create_authenticated(_session(), session_id="session-1"),
                manager.get_or_create_authenticated(_session(), session_id="session-1"),
            )
            assert handles[0] is handles[1]
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_failed_bootstrap_does_not_fall_back_to_public() -> None:
    async def scenario() -> None:
        private_key = Ed25519PrivateKey.generate()
        manager = MCPClientManager(private_key, private_key.public_key())
        manager.users_path = Path("missing-users.json")
        try:
            try:
                await manager.get_or_create_authenticated(_session(), session_id="session-1")
            except Exception:
                pass
            assert manager.state("session-1") is ProcessState.AUSENTE
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_child_environment_does_not_include_parent_secrets() -> None:
    parameters = public_server_parameters()
    assert parameters.env is not None
    assert "TELEGRAM_BOT_TOKEN" not in parameters.env
    assert "OPENAI_API_KEY" not in parameters.env
    assert "COM_UPB_MCP_BOOTSTRAP_TOKEN" not in parameters.env
