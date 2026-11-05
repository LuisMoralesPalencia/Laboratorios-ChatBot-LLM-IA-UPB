import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent import Agent
from llm_client import ToolCall, ToolDefinition
from mcp_client import MCPClientManager, SESSION_META_KEY
from mcp_identity import (
    CONFIRMATION_AUDIENCE,
    CONFIRMATION_TYPE,
    IdentityError,
    canonical_arguments_hash,
    issue_confirmation_token,
    verify_confirmation_token,
)
from sessions import Session, UserRepository

USERS_PATH = Path(__file__).parents[1] / "data" / "usuarios.json"


def _session(telegram_id: str) -> Session:
    user = UserRepository.from_json(USERS_PATH).resolve(telegram_id)
    now = datetime.now(timezone.utc)
    return Session(user.telegram_user_id, user.name, user.role, user.student_code, now, now + timedelta(hours=1))


def _manager() -> MCPClientManager:
    key = Ed25519PrivateKey.generate()
    return MCPClientManager(key, key.public_key())


def test_confirmation_round_trip_and_argument_binding() -> None:
    from mcp_identity import context_from_user

    users = UserRepository.from_json(USERS_PATH)
    context = context_from_user(session_id="confirmation-1", telegram_user_id="9285161265", jti="session-jti", users=users)
    key = Ed25519PrivateKey.generate()
    arguments = {"valor": 4.5, "codigo": "2026001", "evaluacion": "parcial_1"}
    token = issue_confirmation_token(context, key, tool_name="registrar_nota", arguments=arguments)
    assert verify_confirmation_token(token, key.public_key(), expected_context=context, tool_name="registrar_nota", arguments=arguments)
    assert canonical_arguments_hash(arguments) == canonical_arguments_hash(
        {"evaluacion": "parcial_1", "codigo": "2026001", "valor": 4.5}
    )
    try:
        verify_confirmation_token(token, key.public_key(), expected_context=context, tool_name="registrar_nota", arguments={**arguments, "valor": 5})
    except IdentityError:
        pass
    else:
        raise AssertionError("Los argumentos modificados no fueron rechazados")


def test_confirmation_rejects_wrong_purpose_and_expired_token() -> None:
    from mcp_identity import context_from_user

    users = UserRepository.from_json(USERS_PATH)
    context = context_from_user(session_id="confirmation-2", telegram_user_id="9285161265", jti="jti-2", users=users)
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    token = issue_confirmation_token(
        context, key, tool_name="registrar_nota", arguments={"codigo": "2026001"},
        now=now - timedelta(minutes=2), lifetime_seconds=1,
    )
    for bad in (
        jwt.encode({"iss": "wrong", "aud": CONFIRMATION_AUDIENCE, "typ": CONFIRMATION_TYPE}, key, algorithm="EdDSA"),
    ):
        try:
            verify_confirmation_token(bad, key.public_key(), expected_context=context, tool_name="registrar_nota", arguments={"codigo": "2026001"})
        except IdentityError:
            pass
        else:
            raise AssertionError("El comprobante inválido fue aceptado")
    try:
        verify_confirmation_token(token, key.public_key(), expected_context=context, tool_name="registrar_nota", arguments={"codigo": "2026001"})
    except IdentityError:
        pass
    else:
        raise AssertionError("El comprobante vencido fue aceptado")


def test_confirmation_is_consumed_once_and_missing_confirmation_is_rejected() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_authenticated(_session("9285161265"), session_id="teacher-confirm")
            arguments = {"codigo": "2026001", "evaluacion": "parcial_1", "valor": 4.5}
            token = issue_confirmation_token(handle.context, manager.private_key, tool_name="registrar_nota", arguments=arguments)
            first = await manager.call_tool("teacher-confirm", "registrar_nota", arguments, confirmation_token=token)
            second = await manager.call_tool("teacher-confirm", "registrar_nota", arguments, confirmation_token=token)
            assert not first.is_error
            assert second.is_error
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_concurrent_reuse_allows_only_one_write() -> None:
    async def scenario() -> None:
        manager = _manager()
        try:
            handle = await manager.get_or_create_authenticated(_session("6892056328"), session_id="student-confirm")
            arguments = {"parcial": 1, "causa": "incapacidad médica"}
            token = issue_confirmation_token(handle.context, manager.private_key, tool_name="solicitar_supletorio", arguments=arguments)
            results = await asyncio.gather(
                manager.call_tool("student-confirm", "solicitar_supletorio", arguments, confirmation_token=token),
                manager.call_tool("student-confirm", "solicitar_supletorio", arguments, confirmation_token=token),
            )
            assert sum(not result.is_error for result in results) == 1
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_agent_rejects_without_confirm_and_emits_only_after_approval() -> None:
    async def scenario() -> None:
        from tools import Tool

        calls: list[str] = []

        async def run(arguments: dict[str, object]) -> str:
            calls.append("executed")
            return "ok"

        tool = Tool("write", "write", {"type": "object"}, run, False, "test", issue_confirmation=lambda _: _token())

        async def _token() -> str:
            return "confirmation"

        class FakeLLM:
            def chat(self, messages, *, tools=None, allow_tools=True):
                return type("Response", (), {
                    "tool_calls": [ToolCall("1", "write", "{}")],
                    "text": "",
                    "as_message": lambda self: {"role": "assistant", "content": ""},
                })()

        denied = Agent(FakeLLM(), [tool], None)
        result = await denied.execute(ToolCall("1", "write", "{}"))
        assert result.status == "rechazada"
        assert calls == []

        approved = Agent(FakeLLM(), [tool], lambda *_: _approved())

        async def _approved() -> bool:
            return True

        result = await approved.execute(ToolCall("1", "write", "{}"))
        assert result.status == "ok"
        assert calls == ["executed"]

    asyncio.run(scenario())
