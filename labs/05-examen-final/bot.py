"""Adaptador de Telegram para el núcleo del examen final.

Este módulo solo traduce Updates a llamadas del núcleo. La identidad, autorización
y selección de procesos MCP permanecen en SessionManager y Orchestrator.
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dotenv import find_dotenv, load_dotenv
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from llm_client import Message
from orchestrator import Mode, Orchestrator
from prompts import PERSONALIZATION_NAME
from sessions import SessionManager

HistoryKey = tuple[int | str, int | str]
SendConfirmation = Callable[[Any], Awaitable[bool]]

HELP_TEXT = (
    "Comandos disponibles:\n"
    "/start - presentar el asistente\n"
    "/quiensoy - consultar la sesión activa\n"
    "/ayuda - mostrar esta ayuda\n"
    "Escribe una pregunta para consultar el asistente."
)


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
    mode: Mode = "agente"
    history: ConversationHistory = field(default_factory=ConversationHistory)
    started: bool = False

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
        application.add_handler(CommandHandler("ayuda", self.help))
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
