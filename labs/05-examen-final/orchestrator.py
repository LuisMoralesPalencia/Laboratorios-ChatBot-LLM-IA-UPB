"""Orquestador: compone LLM, RAG, tools locales y MCP en un solo asistente.

Dos estrategias para decidir qué capacidad usar:

  modo "router" (flujo fijo, decidido por el software después de clasificar):
      pregunta → router → "directa"   → LLM बिना tools           (como el Lab 01)
                        → "documentos" → CourseRAG.answer          (como el Lab 02)
                        → "acciones"   → agente con tools y MCP    (como el Lab 03, con ciclo)

  modo "agente" (flujo dinámico, decidido por el modelo en cada paso):
      pregunta → agente con TODAS las capacidades como tools (incluida buscar_documentos)

El orquestador no sabe nada de la consola: recibe una pregunta y devuelve la respuesta
con su traza (ruta, pasos, fuentes, llamadas al LLM).
"""

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Literal

from agent import MAX_STEPS, Agent, AgentAnswer, AgentStep, ConfirmFn
from capabilities import rag_tool
from config import load_rag_settings, load_settings
from embeddings import Embedder
from llm_client import LLMClient, LLMResponse, Message
from mcp_client import load_mcp_tools, open_client
from prompts import build_direct_messages
from rag import CourseRAG
from router import RouterResult, route
from sessions import SessionManager, TelegramUserId
from tools import LOCAL_TOOLS, Tool
from vector_store import VectorStore

Mode = Literal["router", "agente"]


@dataclass
class OrchestratorAnswer:
    text: str
    mode: Mode
    routing: RouterResult | None = None  # solo en modo router
    steps: list[AgentStep] = field(default_factory=list)  # traza del agente, si se usó
    sources: list[str] = field(default_factory=list)  # fuentes calculadas por el programa (ruta documentos)
    responses: list[LLMResponse] = field(default_factory=list)  # TODAS las llamadas al LLM, en orden
    stopped_by_limit: bool = False

    def tools_used(self) -> list[str]:
        return [run.call.name for step in self.steps for run in step.tool_runs]


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        rag: CourseRAG,
        action_tools: list[Tool],
        confirm: ConfirmFn | None = None,
        max_steps: int = MAX_STEPS,
        *,
        session_manager: SessionManager | None = None,
        mcp_manager=None,
    ):
        self.llm = llm
        self.rag = rag
        # Modo agente: un solo agente con todas las capacidades.
        self.action_tools = action_tools
        self.confirm = confirm
        self.max_steps = max_steps
        self.session_manager = session_manager
        self.mcp_manager = mcp_manager
        self._session_ids_by_user: dict[str, str] = {}
        self.agent = Agent(llm, [rag_tool(rag), *action_tools], confirm, max_steps)
        self.actions_agent = Agent(llm, action_tools, confirm, max_steps)

    async def answer(
        self,
        question: str,
        history: list[Message],
        mode: Mode,
        *,
        telegram_user_id: TelegramUserId | None = None,
        confirm: ConfirmFn | None = None,
    ) -> OrchestratorAnswer:
        try:
            action_tools, role = await self._tools_for_request(telegram_user_id)
            callback = confirm if confirm is not None else self.confirm
            agent = Agent(self.llm, [rag_tool(self.rag, role), *action_tools], callback, self.max_steps)
            actions_agent = Agent(self.llm, action_tools, callback, self.max_steps)
        except Exception as exc:
            return self._controlled_error(mode, f"No se pudo preparar el contexto de la consulta: {exc}")

        if mode == "agente":
            try:
                return self._from_agent(await agent.run(question, history), mode)
            except Exception as exc:
                return self._controlled_error(mode, f"No se pudo procesar la consulta: {exc}")

        try:
            routing = route(self.llm, question)
            ruta = routing.decision.ruta
        # TODO 5: ejecuta el camino de la ruta elegida y construye un OrchestratorAnswer (sin routing).
        #   - "directa":    response = self.llm.chat(build_direct_messages(history, question))
        #                   → OrchestratorAnswer(response.text, mode, responses=[response])
        #   - "documentos": rag_answer = self.rag.answer(question, history)   (el pipeline del Lab 02)
        #                   → incluye sources: los nombres de archivo (result.chunk.source) de
        #                     rag_answer.sources, sin repetir y ordenados; responses=[rag_answer.response]
        #   - "acciones":   self._from_agent(await self.actions_agent.run(question, history), mode)
        #   Después: answer.routing = routing, y agrega routing.response AL INICIO de answer.responses
        #   (la clasificación también fue una llamada al LLM). Devuelve answer.
        
            if ruta == "directa":
                response = self.llm.chat(build_direct_messages(history, question))
                answer = OrchestratorAnswer(text=response.text, mode=mode, responses=[response])
            elif ruta == "documentos":
                rag_answer = self.rag.answer(question, history, role=role)
                sources = sorted(list(set(result.chunk.source for result in rag_answer.sources)))
                answer = OrchestratorAnswer(
                    text=rag_answer.text,
                    mode=mode,
                    sources=sources,
                    responses=[rag_answer.response],
                )
            elif ruta == "acciones":
                agent_answer = await actions_agent.run(question, history)
                answer = self._from_agent(agent_answer, mode)
            else:
                response = self.llm.chat(build_direct_messages(history, question))
                answer = OrchestratorAnswer(text=response.text, mode=mode, responses=[response])
        except Exception as exc:
            return self._controlled_error(mode, f"No se pudo procesar la consulta: {exc}")

        answer.routing = routing
        answer.responses.insert(0, routing.response)
        return answer

    async def _tools_for_request(self, telegram_user_id: TelegramUserId | None) -> tuple[list[Tool], str]:
        if self.session_manager is None or self.mcp_manager is None:
            return self.action_tools, "publico"

        if telegram_user_id is None:
            await self.mcp_manager.get_or_create_public()
            tools = await self.mcp_manager.load_tools(None)
            return [*LOCAL_TOOLS, *tools], "publico"

        normalized = str(telegram_user_id).strip()
        session = self.session_manager.get_active_session(telegram_user_id)
        previous_session_id = self._session_ids_by_user.get(normalized)
        if session is None:
            if previous_session_id is not None:
                await self.mcp_manager.close_session(previous_session_id)
                self._session_ids_by_user.pop(normalized, None)
            raise PermissionError("No existe una sesión autenticada activa.")

        self._session_ids_by_user[normalized] = session.session_id
        await self.mcp_manager.get_or_create_authenticated(
            session,
            session_id=session.session_id,
        )
        tools = await self.mcp_manager.load_tools(session.session_id)
        return [*LOCAL_TOOLS, *tools], session.role

    @staticmethod
    def _controlled_error(mode: Mode, message: str) -> OrchestratorAnswer:
        return OrchestratorAnswer(text=f"[error] {message}", mode=mode)

    @staticmethod
    def _from_agent(result: AgentAnswer, mode: Mode) -> OrchestratorAnswer:
        return OrchestratorAnswer(
            result.text,
            mode,
            steps=result.steps,
            responses=[step.response for step in result.steps],
            stopped_by_limit=result.stopped_by_limit,
        )


async def create_orchestrator(
    stack: AsyncExitStack,
    confirm: ConfirmFn | None = None,
    *,
    session_manager: SessionManager | None = None,
    mcp_manager=None,
) -> Orchestrator:
    """Arma el sistema completo: configuración, LLM, índice, RAG, tools locales y servidor MCP.

    Lanza ValueError (configuración), FileNotFoundError (falta el índice) o un error de
    conexión con el servidor MCP. El servidor queda abierto hasta que se cierre `stack`.
    """
    settings = load_settings()
    rag_settings = load_rag_settings()
    store = VectorStore.load(rag_settings.index_dir)

    llm = LLMClient(settings)
    rag = CourseRAG(
        llm=llm,
        # La pregunta se convierte con el mismo modelo que indexó los documentos.
        embedder=Embedder(store.embedding_model),
        store=store,
        top_k=rag_settings.top_k,
        min_score=rag_settings.min_score,
    )
    if mcp_manager is not None:
        return Orchestrator(
            llm,
            rag,
            list(LOCAL_TOOLS),
            confirm,
            session_manager=session_manager,
            mcp_manager=mcp_manager,
        )

    mcp_client = await open_client(stack)
    action_tools = [*LOCAL_TOOLS, *await load_mcp_tools(mcp_client)]
    return Orchestrator(llm, rag, action_tools, confirm)