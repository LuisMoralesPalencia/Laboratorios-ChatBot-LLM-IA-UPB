from types import SimpleNamespace

import pytest

from bot import ConversationHistory, TelegramBot, build_application, telegram_token
from prompts import AGENT_SYSTEM_PROMPT, PERSONALIZATION_NAME


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id, chat_id, text=None):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage(text)


class FakeSessions:
    def __init__(self, session=None):
        self.session = session
        self.ids = []

    def get_active_session(self, user_id):
        self.ids.append(user_id)
        return self.session


class FakeOrchestrator:
    def __init__(self, text="respuesta"):
        self.text = text
        self.calls = []

    async def answer(self, question, history, mode, *, telegram_user_id=None, confirm=None):
        self.calls.append((question, list(history), mode, telegram_user_id))
        return SimpleNamespace(text=self.text)


class FakeMCP:
    def __init__(self):
        self.public_started = 0
        self.closed = 0

    async def get_or_create_public(self):
        self.public_started += 1

    async def close_all(self):
        self.closed += 1


async def invoke(handler, update):
    await handler(update, SimpleNamespace())


def make_bot(session=None, orchestrator=None):
    return TelegramBot(
        orchestrator or FakeOrchestrator(),
        FakeSessions(session),
        FakeMCP(),
    )


@pytest.mark.asyncio
async def test_start_quiensoy_and_help():
    bot = make_bot()
    update = FakeUpdate(10, 20, "/start")
    await invoke(bot.start, update)
    assert PERSONALIZATION_NAME in update.effective_message.replies[0]

    update = FakeUpdate(10, 20)
    await invoke(bot.whoami, update)
    assert "iniciar sesión" in update.effective_message.replies[0]

    update = FakeUpdate(10, 20)
    await invoke(bot.help, update)
    assert "/quiensoy" in update.effective_message.replies[0]


@pytest.mark.asyncio
async def test_whoami_uses_trusted_session_data():
    session = SimpleNamespace(name="Ana", role="estudiante")
    sessions = FakeSessions(session)
    bot = TelegramBot(FakeOrchestrator(), sessions, FakeMCP())
    update = FakeUpdate(123, 20)
    await invoke(bot.whoami, update)
    assert sessions.ids == [123]
    assert "Ana (estudiante)" in update.effective_message.replies[0]


@pytest.mark.asyncio
async def test_text_passes_update_identity_and_keeps_isolated_history():
    orchestrator = FakeOrchestrator()
    bot = make_bot(orchestrator=orchestrator)
    first = FakeUpdate(1, 100, "hola")
    second = FakeUpdate(1, 100, "segunda")
    other = FakeUpdate(2, 200, "otra")
    await invoke(bot.text, first)
    await invoke(bot.text, second)
    await invoke(bot.text, other)

    assert orchestrator.calls[0][3] == 1
    assert orchestrator.calls[1][1] == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "respuesta"},
    ]
    assert orchestrator.calls[2][1] == []


@pytest.mark.asyncio
async def test_group_users_are_isolated_and_empty_or_non_text_is_ignored():
    orchestrator = FakeOrchestrator()
    bot = make_bot(orchestrator=orchestrator)
    await invoke(bot.text, FakeUpdate(1, -10, "uno"))
    await invoke(bot.text, FakeUpdate(2, -10, "dos"))
    await invoke(bot.text, FakeUpdate(1, -10, None))
    assert len(orchestrator.calls) == 2
    assert orchestrator.calls[1][1] == []


@pytest.mark.asyncio
async def test_core_errors_are_reported_without_crashing():
    class Broken:
        async def answer(self, *args, **kwargs):
            raise RuntimeError("internal detail")

    bot = make_bot(orchestrator=Broken())
    update = FakeUpdate(1, 10, "consulta")
    await invoke(bot.text, update)
    assert update.effective_message.replies == [
        "No pude procesar tu consulta en este momento."
    ]


def test_history_is_bounded():
    history = ConversationHistory(max_messages=2)
    history.append((1, 1), "a", "A")
    history.append((1, 1), "b", "B")
    assert history.get((1, 1)) == [
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "B"},
    ]


def test_system_prompt_and_application_registration(monkeypatch):
    assert PERSONALIZATION_NAME in AGENT_SYSTEM_PROMPT
    bot = make_bot()
    application = build_application(bot, token="test-token")
    assert application.bot.token == "test-token"


def test_telegram_token_is_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-test-token")
    assert telegram_token() == "secret-test-token"
    assert "secret-test-token" not in caplog.text


@pytest.mark.asyncio
async def test_mcp_lifecycle_runs_once_at_startup_and_shutdown():
    bot = make_bot()
    application = SimpleNamespace()
    await bot.initialize(application)
    await bot.initialize(application)
    await bot.shutdown(application)
    await bot.shutdown(application)
    assert bot.mcp_manager.public_started == 1
    assert bot.mcp_manager.closed == 1
