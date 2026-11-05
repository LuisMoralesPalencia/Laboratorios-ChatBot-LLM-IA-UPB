"""Adaptador de Telegram para el núcleo del examen final.

Este módulo solo traduce Updates a llamadas del núcleo. La identidad, autorización
y selección de procesos MCP permanecen en SessionManager y Orchestrator.
"""

from __future__ import annotations

import os
import asyncio
import tempfile
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from llm_client import Message
from orchestrator import Mode, Orchestrator
from prompts import PERSONALIZATION_NAME
from sessions import SessionManager
from liveness import LivenessResult, LivenessService, VideoNoteMetadata
from mcp_client import ProcessState

HistoryKey = tuple[int | str, int | str]
SendConfirmation = Callable[[Any], Awaitable[bool]]

HELP_TEXT = (
    "Comandos disponibles:\n"
    "/start - presentar el asistente\n"
    "/quiensoy - consultar la sesión activa\n"
    "/login - iniciar autenticación biométrica\n"
    "/logout - cerrar la sesión activa\n"
    "/ayuda - mostrar esta ayuda\n"
    "Escribe una pregunta para consultar el asistente."
)

CHALLENGE_INSTRUCTIONS = {
    "girar_izquierda": "Gira el rostro hacia la izquierda.",
    "girar_derecha": "Gira el rostro hacia la derecha.",
    "acercar_rostro": "Acerca el rostro a la cámara.",
}


class ConversationHistory:
    """Historial acotado por chat y remitente, sin datos de autenticación."""

    def __init__(self, max_messages: int = 20) -> None:
        if max_messages <= 0:
            raise ValueError("max_messages debe ser mayor que cero.")
        self.max_messages = max_messages
        self._histories: defaultdict[HistoryKey, deque[Message]] = defaultdict(
            lambda: deque(maxlen=self.max_messages)
        )

    def get(self, key: HistoryKey) -> list[Message]:
        return list(self._histories[key])

    def append(self, key: HistoryKey, user_text: str, assistant_text: str) -> None:
        history = self._histories[key]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})

    def clear(self, key: HistoryKey) -> None:
        self._histories.pop(key, None)


@dataclass
class TelegramBot:
    orchestrator: Orchestrator
    session_manager: SessionManager
    mcp_manager: Any
    liveness: LivenessService | None = None
    mode: Mode = "agente"
    history: ConversationHistory = field(default_factory=ConversationHistory)
    started: bool = False
    _login_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)
    _login_attempts: dict[str, tuple[int, str]] = field(default_factory=dict, init=False)
    _login_versions: defaultdict[str, int] = field(
        default_factory=lambda: defaultdict(int), init=False
    )

    def _identity(self, update: Update) -> tuple[int | str, int | str] | None:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return None
        return chat.id, user.id

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        message = update.effective_message
        if message is not None:
            await message.reply_text(text)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(
            update,
            f"Soy el Asistente Inteligente del Curso de Inteligencia Artificial. "
            f"Fui desarrollado por {PERSONALIZATION_NAME}. "
            "Puedes hacerme preguntas del curso; para información personal debes iniciar sesión.",
        )

    async def whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None:
            await self._reply(update, "No se pudo identificar al usuario de Telegram.")
            return
        session = self.session_manager.get_active_session(user.id)
        if session is None:
            await self._reply(update, "No tienes una sesión activa. Debes iniciar sesión.")
            return
        await self._reply(update, f"Sesión activa: {session.name} ({session.role}).")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, HELP_TEXT)

    def _login_lock(self, user_id: int | str) -> asyncio.Lock:
        key = str(user_id)
        lock = self._login_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._login_locks[key] = lock
        return lock

    def _audit(self, user_id: int | str, event: str, reason: str | None = None) -> None:
        self.session_manager.audit.record(
            telegram_user_id=user_id, event=event, reason=reason
        )

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or self.liveness is None:
            await self._reply(update, "No se pudo iniciar la autenticación.")
            return
        user_id = user.id
        lock = self._login_lock(user_id)
        async with lock:
            if self.session_manager.get_active_session(user_id) is not None:
                await self._reply(update, "Ya tienes una sesión activa. Usa /logout antes de iniciar otra.")
                return
            if self.session_manager.is_blocked(user_id):
                await self._reply(update, "Tu cuenta está temporalmente bloqueada. Intenta más tarde.")
                return
            if str(user_id) in self._login_attempts:
                await self._reply(update, "Ya tienes un reto de autenticación pendiente.")
                return
            challenge = self.liveness.challenges.issue(user_id)
            version = self._login_versions[str(user_id)] + 1
            self._login_versions[str(user_id)] = version
            self._login_attempts[str(user_id)] = (version, challenge.challenge_id)
            self._audit(user_id, "reto_iniciado")
        instructions = CHALLENGE_INSTRUCTIONS.get(challenge.action, challenge.action)
        await self._reply(
            update,
            f"Reto de autenticación: {instructions} "
            "Responde únicamente con un video-note de Telegram, no con una fotografía ni un video convencional. "
            f"El reto vence en {self.liveness.challenges.timeout_seconds} segundos.",
        )

    @staticmethod
    def _is_forwarded(message: Any) -> bool:
        return getattr(message, "forward_origin", None) is not None or (
            getattr(message, "forward_date", None) is not None
        )

    async def video_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None or self.liveness is None:
            return
        user_id = user.id
        key = str(user_id)
        lock = self._login_lock(user_id)
        async with lock:
            attempt = self._login_attempts.get(key)
            version = self._login_versions[key]
        if attempt is None:
            await self._reply(update, "No tienes un reto de autenticación pendiente.")
            return
        challenge_version, challenge_id = attempt
        if challenge_version != version:
            await self._reply(update, "El reto de autenticación ya no es válido.")
            return

        video_note = getattr(message, "video_note", None)
        is_video_note = video_note is not None
        if not is_video_note:
            await self._process_invalid_media(update, user_id, challenge_id, version)
            return

        temporary_path: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="upb-video-note-") as directory:
                temporary_path = Path(directory) / "video-note"
                telegram_file = await context.bot.get_file(video_note.file_id)
                await telegram_file.download_to_drive(custom_path=str(temporary_path))
                metadata = VideoNoteMetadata(
                    True,
                    self._is_forwarded(message),
                    user_id,
                    temporary_path,
                )
                result = await asyncio.to_thread(
                    self.liveness.process_temporary_video_note,
                    metadata,
                    challenge_id,
                )
                await self._finish_login(update, user_id, version, challenge_id, result)
        except Exception:
            await self._reply(update, "No se pudo procesar la autenticación.")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _process_invalid_media(
        self, update: Update, user_id: int | str, challenge_id: str, version: int
    ) -> None:
        metadata = VideoNoteMetadata(False, False, user_id, Path("__invalid__"))
        result = await asyncio.to_thread(
            self.liveness.process_video_note, metadata, challenge_id
        )
        await self._finish_login(update, user_id, version, challenge_id, result)

    async def _finish_login(
        self,
        update: Update,
        user_id: int | str,
        version: int,
        challenge_id: str,
        result: LivenessResult,
    ) -> None:
        session = result.session
        if not result.accepted or session is None:
            async with self._login_lock(user_id):
                self._login_attempts.pop(str(user_id), None)
            await self._reply(update, "No se pudo verificar tu identidad.")
            return
        if session.telegram_user_id != str(user_id) or not session.session_id:
            self.session_manager.logout(user_id)
            await self._reply(update, "No se pudo completar la autenticación.")
            return

        lock = self._login_lock(user_id)
        async with lock:
            current = self._login_attempts.get(str(user_id))
            if current != (version, challenge_id) or self._login_versions[str(user_id)] != version:
                self.session_manager.logout(user_id)
                await self.mcp_manager.close_session(session.session_id)
                return
            try:
                handle = await self.mcp_manager.get_or_create_authenticated(
                    session, session_id=session.session_id
                )
                if handle.state is not ProcessState.ACTIVO:
                    raise RuntimeError("El proceso MCP no quedó activo.")
            except Exception:
                self.session_manager.logout(user_id)
                await self.mcp_manager.close_session(session.session_id)
                self._login_attempts.pop(str(user_id), None)
                self._audit(user_id, "login_rechazado", "mcp_inicializacion")
                await self._reply(update, "No se pudo completar la autenticación.")
                return
            self._login_attempts.pop(str(user_id), None)
        await self._reply(update, f"Autenticación exitosa. Bienvenido/a, {session.name}.")

    async def logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None:
            return
        user_id = user.id
        async with self._login_lock(user_id):
            self._login_versions[str(user_id)] += 1
            self._login_attempts.pop(str(user_id), None)
            if self.liveness is not None:
                self.liveness.invalidate_for_user(user_id)
            session = self.session_manager.get_active_session(user_id)
            if session is not None:
                self.session_manager.logout(user_id)
                await self.mcp_manager.close_session(session.session_id)
                self._audit(user_id, "logout")
                await self._reply(update, "Sesión cerrada correctamente.")
                return
            self._audit(user_id, "login_cancelado", "logout")
            await self._reply(update, "No había una sesión activa. El reto pendiente fue cancelado.")

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        identity = self._identity(update)
        message = update.effective_message
        question = message.text.strip() if message is not None and message.text else ""
        if identity is None or not question:
            return

        try:
            answer = await self.orchestrator.answer(
                question,
                self.history.get(identity),
                self.mode,
                telegram_user_id=identity[1],
            )
        except Exception:
            await self._reply(update, "No pude procesar tu consulta en este momento.")
            return

        self.history.append(identity, question, answer.text)
        await self._reply(update, answer.text)

    async def initialize(self, application: Application) -> None:
        if self.started:
            return
        await self.mcp_manager.get_or_create_public()
        self.started = True

    async def shutdown(self, application: Application) -> None:
        if self.started:
            await self.mcp_manager.close_all()
            self.started = False

    def register_handlers(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("quiensoy", self.whoami))
        application.add_handler(CommandHandler("login", self.login))
        application.add_handler(CommandHandler("logout", self.logout))
        application.add_handler(CommandHandler("ayuda", self.help))
        application.add_handler(MessageHandler(filters.VIDEO_NOTE, self.video_note))
        application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, self.video_note))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))


def telegram_token() -> str:
    """Carga el token sin incluirlo en mensajes ni registros."""
    load_dotenv(find_dotenv(usecwd=True))
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en el archivo .env.")
    return token


def build_application(bot: TelegramBot, token: str | None = None) -> Application:
    application = (
        ApplicationBuilder()
        .token(token if token is not None else telegram_token())
        .post_init(bot.initialize)
        .post_shutdown(bot.shutdown)
        .build()
    )
    bot.register_handlers(application)
    return application
