import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from bot import ConversationHistory, TelegramBot, build_application, telegram_token
from liveness import ChallengeManager, LivenessResult
from mcp_client import ProcessState
from prompts import AGENT_SYSTEM_PROMPT, PERSONALIZATION_NAME
from sessions import Session
from tools import Tool


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_markup = kwargs.get("reply_markup")


class FakeUpdate:
    def __init__(self, user_id, chat_id, text=None):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage(text)


class FakeSessions:
    def __init__(self, session=None):
        self.session = session
        self.ids = []
        self.audit = SimpleNamespace(record=lambda **kwargs: None)

    def get_active_session(self, user_id):
        self.ids.append(user_id)
        return self.session

    def is_blocked(self, user_id):
        return False

    def logout(self, user_id):
        self.session = None


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
        self.authenticated = []
        self.fail = False

    async def get_or_create_public(self):
        self.public_started += 1

    async def close_all(self):
        self.closed += 1

    async def get_or_create_authenticated(self, session, *, session_id):
        if self.fail:
            raise RuntimeError("mcp")
        self.authenticated.append((session.telegram_user_id, session_id))
        return SimpleNamespace(state=ProcessState.ACTIVO)

    async def close_session(self, session_id):
        self.closed += 1


class FakeCallbackQuery:
    def __init__(self, data, user_id, chat_id):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
        self.answered = 0
        self.edited = 0

    async def answer(self):
        self.answered += 1

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edited += 1


class FakeCallbackUpdate:
    def __init__(self, query):
        self.callback_query = query


class FakeLiveness:
    def __init__(self, challenges, result):
        self.challenges = challenges
        self.result = result
        self.calls = []

    def invalidate_for_user(self, user_id):
        self.challenges.invalidate_for_user(user_id)

    def process_temporary_video_note(self, metadata, challenge_id):
        self.calls.append((metadata, challenge_id))
        metadata.video_path.unlink(missing_ok=True)
        return self.result

    def process_video_note(self, metadata, challenge_id):
        self.calls.append((metadata, challenge_id))
        return self.result


async def invoke(handler, update):
    await handler(update, SimpleNamespace())


def make_bot(session=None, orchestrator=None):
    return TelegramBot(
        orchestrator or FakeOrchestrator(),
        FakeSessions(session),
        FakeMCP(),
    )


def make_login_bot(result=None):
    sessions = FakeSessions()
    challenges = ChallengeManager(
        timeout_seconds=60,
        random_source=lambda values: values[0],
        id_source=lambda: "challenge-login",
    )
    liveness = FakeLiveness(challenges, result or LivenessResult(False, "no", "fail"))
    bot = TelegramBot(FakeOrchestrator(), sessions, FakeMCP(), liveness=liveness)
    return bot, sessions, liveness


def write_tool(name="solicitar_supletorio"):
    return Tool(
        name=name,
        description="write",
        parameters={},
        run=lambda arguments: None,
        read_only=False,
        origin="mcp",
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


@pytest.mark.asyncio
async def test_login_creates_challenge_and_rejects_second_login():
    bot, sessions, _ = make_login_bot()
    update = FakeUpdate(1, 10, "/login")
    await invoke(bot.login, update)
    assert "video-note" in update.effective_message.replies[0]
    assert sessions.ids == [1]
    second = FakeUpdate(1, 10, "/login")
    await invoke(bot.login, second)
    assert "reto" in second.effective_message.replies[0]


@pytest.mark.asyncio
async def test_login_rejects_blocked_user():
    class Blocked(FakeSessions):
        def is_blocked(self, user_id):
            return True

    bot, _, _ = make_login_bot()
    bot.session_manager = Blocked()
    update = FakeUpdate(1, 10, "/login")
    await invoke(bot.login, update)
    assert "bloqueada" in update.effective_message.replies[0]


@pytest.mark.asyncio
async def test_valid_video_note_creates_mcp_process_with_session_id(tmp_path):
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-1")
    result = LivenessResult(True, "ok", "ok", session=session)
    bot, _, liveness = make_login_bot(result)
    update = FakeUpdate(1, 10, "/login")
    await invoke(bot.login, update)
    update = FakeUpdate(1, 10)
    update.effective_message.video_note = SimpleNamespace(file_id="file")

    class File:
        async def download_to_drive(self, custom_path):
            Path(custom_path).write_bytes(b"video")

    class Context:
        class bot:
            @staticmethod
            async def get_file(_):
                return File()

    await bot.video_note(update, Context())
    assert bot.mcp_manager.authenticated == [("1", "sid-1")]
    assert "exitosa" in update.effective_message.replies[0]
    assert liveness.calls[0][0].is_video_note is True


@pytest.mark.asyncio
async def test_forwarded_video_note_is_rejected_without_mcp():
    bot, _, liveness = make_login_bot()
    update = FakeUpdate(1, 10)
    await invoke(bot.login, update)
    update.effective_message.video_note = SimpleNamespace(file_id="file")
    update.effective_message.forward_origin = object()

    class File:
        async def download_to_drive(self, custom_path):
            Path(custom_path).write_bytes(b"video")

    class Context:
        class bot:
            @staticmethod
            async def get_file(_):
                return File()

    await bot.video_note(update, Context())
    assert "verificar" in update.effective_message.replies[-1]
    assert liveness.calls[0][0].is_forwarded is True
    assert bot.mcp_manager.authenticated == []


@pytest.mark.asyncio
async def test_logout_cancels_challenge_without_closing_public_process():
    bot, _, _ = make_login_bot()
    await invoke(bot.login, FakeUpdate(1, 10))
    update = FakeUpdate(1, 10)
    await invoke(bot.logout, update)
    assert "cancelado" in update.effective_message.replies[0]
    with pytest.raises(ValueError):
        bot.liveness.challenges.consume("challenge-login", 1)
    assert bot.mcp_manager.closed == 0


@pytest.mark.asyncio
async def test_mcp_failure_invalidates_created_session():
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-2")
    bot, sessions, _ = make_login_bot(LivenessResult(True, "ok", "ok", session=session))
    bot.mcp_manager.fail = True
    await invoke(bot.login, FakeUpdate(1, 10))
    update = FakeUpdate(1, 10)
    update.effective_message.video_note = SimpleNamespace(file_id="file")

    class File:
        async def download_to_drive(self, custom_path):
            Path(custom_path).write_bytes(b"video")

    class Context:
        class bot:
            @staticmethod
            async def get_file(_):
                return File()

    await bot.video_note(update, Context())
    assert "completar" in update.effective_message.replies[-1]
    assert bot.mcp_manager.authenticated == []


@pytest.mark.asyncio
async def test_confirmation_sends_opaque_buttons_and_confirm_resolves_true():
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-confirm")
    bot, sessions, _ = make_login_bot()
    sessions.session = session
    update = FakeUpdate(1, 10, "consulta")
    task = asyncio.create_task(
        bot._confirm_for(
            update,
            SimpleNamespace(),
            write_tool(),
            {"parcial": "parcial_1", "causa": "fuerza mayor"},
        )
    )
    while not bot._confirmations:
        await asyncio.sleep(0)
    identifier, pending = next(iter(bot._confirmations.items()))
    markup = update.effective_message.reply_markup
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert identifier in callback_data
    assert "fuerza mayor" not in callback_data
    await bot.confirmation_callback(
        FakeCallbackUpdate(FakeCallbackQuery(callback_data, 1, 10)),
        SimpleNamespace(),
    )
    assert await task is True


@pytest.mark.asyncio
async def test_reject_timeout_and_wrong_identity_do_not_approve():
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-confirm")
    bot, sessions, _ = make_login_bot()
    sessions.session = session
    update = FakeUpdate(1, 10, "consulta")
    task = asyncio.create_task(
        bot._confirm_for(
            update, SimpleNamespace(), write_tool(),
            {"parcial": "p1", "causa": "causa"},
        )
    )
    while not bot._confirmations:
        await asyncio.sleep(0)
    identifier = next(iter(bot._confirmations))
    query = FakeCallbackQuery(f"confirm:yes:{identifier}", 2, 10)
    await bot.confirmation_callback(FakeCallbackUpdate(query), SimpleNamespace())
    assert not task.done()
    await bot.cancel_confirmations(telegram_user_id=1)
    assert await task is False
    assert query.answered == 1


@pytest.mark.asyncio
async def test_two_confirmations_are_independent_and_double_callback_wins_once():
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-confirm")
    bot, sessions, _ = make_login_bot()
    sessions.session = session
    first_update = FakeUpdate(1, 10, "a")
    second_update = FakeUpdate(1, 10, "b")
    first = asyncio.create_task(
        bot._confirm_for(first_update, SimpleNamespace(), write_tool(),
                         {"parcial": "p1", "causa": "a"})
    )
    second = asyncio.create_task(
        bot._confirm_for(second_update, SimpleNamespace(), write_tool(),
                         {"parcial": "p2", "causa": "b"})
    )
    while len(bot._confirmations) < 2:
        await asyncio.sleep(0)
    identifiers = list(bot._confirmations)
    await bot.confirmation_callback(
        FakeCallbackUpdate(FakeCallbackQuery(f"confirm:yes:{identifiers[0]}", 1, 10)),
        SimpleNamespace(),
    )
    await bot.confirmation_callback(
        FakeCallbackUpdate(FakeCallbackQuery(f"confirm:no:{identifiers[0]}", 1, 10)),
        SimpleNamespace(),
    )
    await bot.confirmation_callback(
        FakeCallbackUpdate(FakeCallbackQuery(f"confirm:no:{identifiers[1]}", 1, 10)),
        SimpleNamespace(),
    )
    assert await first is True
    assert await second is False


@pytest.mark.asyncio
async def test_logout_and_shutdown_cancel_pending_confirmations():
    session = Session("1", "Ana", "estudiante", "S-1", None, None, "sid-confirm")
    bot, sessions, _ = make_login_bot()
    sessions.session = session
    task = asyncio.create_task(
        bot._confirm_for(
            FakeUpdate(1, 10, "consulta"), SimpleNamespace(), write_tool(),
            {"parcial": "p1", "causa": "causa"},
        )
    )
    while not bot._confirmations:
        await asyncio.sleep(0)
    await bot.logout(FakeUpdate(1, 10), SimpleNamespace())
    assert await task is False
    assert not bot._confirmations
