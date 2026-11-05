import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit import AuditLogger
from orchestrator import Orchestrator
from sessions import SessionManager, UserRepository


class FakeManager:
    def __init__(self) -> None:
        self.public_calls = 0
        self.authenticated_calls: list[tuple[str, str]] = []
        self.loaded: list[str | None] = []
        self.closed: list[str] = []

    async def get_or_create_public(self):
        self.public_calls += 1
        return object()

    async def load_tools(self, session_id: str | None = None):
        self.loaded.append(session_id)
        return []

    async def get_or_create_authenticated(self, session, *, session_id: str):
        self.authenticated_calls.append((session.telegram_user_id, session_id))
        return object()

    async def close_session(self, session_id: str):
        self.closed.append(session_id)


def _session_manager(tmp_path: Path, clock):
    users = {
        "usuarios": [
            {
                "id": "student-1",
                "nombre": "Estudiante 1",
                "rol": "estudiante",
                "codigo_estudiante": "S-1",
                "telegram_user_id": 101,
            },
            {
                "id": "student-2",
                "nombre": "Estudiante 2",
                "rol": "estudiante",
                "codigo_estudiante": "S-2",
                "telegram_user_id": 202,
            },
            {
                "id": "teacher",
                "nombre": "Docente",
                "rol": "docente",
                "codigo_estudiante": None,
                "telegram_user_id": 303,
            },
        ]
    }
    users_path = tmp_path / "usuarios.json"
    users_path.write_text(json.dumps(users), encoding="utf-8")
    return SessionManager(
        UserRepository.from_json(users_path),
        AuditLogger(tmp_path / "audit.jsonl"),
        session_minutes=10,
        clock=clock,
    )


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 5, 7, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def _orchestrator(session_manager, manager):
    return Orchestrator(
        object(),
        object(),
        [],
        None,
        session_manager=session_manager,
        mcp_manager=manager,
    )


def test_public_and_authenticated_contexts_are_selected_by_session(tmp_path: Path) -> None:
    async def scenario(tmp_path: Path) -> None:
        clock = Clock()
        sessions = _session_manager(tmp_path, clock)
        manager = FakeManager()
        orchestrator = _orchestrator(sessions, manager)

        public_tools, public_role = await orchestrator._tools_for_request(None)
        assert {tool.name for tool in public_tools} == {
            "fecha_actual",
            "dias_hasta",
            "calcular_nota_final",
        }
        assert public_role == "publico"
        assert manager.public_calls == 1
        assert manager.loaded == [None]

        student = sessions.create_authenticated_session(101)
        tools, role = await orchestrator._tools_for_request(101)
        assert {tool.name for tool in tools} == {
            "fecha_actual",
            "dias_hasta",
            "calcular_nota_final",
        }
        assert role == "estudiante"
        assert manager.authenticated_calls == [(str(101), student.session_id)]
        assert manager.loaded[-1] == student.session_id

    asyncio.run(scenario(tmp_path))


def test_users_and_sessions_never_share_authenticated_process(tmp_path: Path) -> None:
    async def scenario(tmp_path: Path) -> None:
        clock = Clock()
        sessions = _session_manager(tmp_path, clock)
        manager = FakeManager()
        orchestrator = _orchestrator(sessions, manager)
        first = sessions.create_authenticated_session(101)
        second = sessions.create_authenticated_session(202)
        await orchestrator._tools_for_request(101)
        await orchestrator._tools_for_request(202)
        assert first.session_id != second.session_id
        assert manager.authenticated_calls == [
            ("101", first.session_id),
            ("202", second.session_id),
        ]

    asyncio.run(scenario(tmp_path))


def test_expired_session_closes_process_and_never_falls_back_public(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = Clock()
        sessions = _session_manager(tmp_path, clock)
        manager = FakeManager()
        orchestrator = _orchestrator(sessions, manager)
        session = sessions.create_authenticated_session(101)
        await orchestrator._tools_for_request(101)
        clock.advance(minutes=10)
        try:
            await orchestrator._tools_for_request(101)
        except PermissionError:
            pass
        else:
            raise AssertionError("La sesión expirada fue aceptada")
        assert manager.closed == [session.session_id]
        assert manager.loaded == [session.session_id]

    asyncio.run(scenario())
