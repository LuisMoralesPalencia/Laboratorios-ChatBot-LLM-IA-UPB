"""Asistente con tools: el LLM PIDE, el programa EJECUTA.

Cada pregunta sigue tres pasos (una sola ronda de tools):
    1. pedir:     mensajes + definiciones de tools → LLM → texto o tool_calls
    2. ejecutar:  el programa valida, confirma las acciones y ejecuta cada tool pedida
    3. responder: mensajes + resultados (rol "tool") → LLM → respuesta final

CourseAssistant no sabe nada de la consola ni de si una tool es local o viene de un
servidor MCP: recibe una lista de `Tool` y una función para confirmar acciones.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from llm_client import LLMClient, LLMResponse, Message, ToolCall
from prompts import build_messages
from tools import Tool

# Quien usa el asistente (la interfaz) decide cómo pedir autorización al usuario.
ConfirmFn = Callable[[Tool, dict[str, Any]], Awaitable[bool]]

REJECTED = "El usuario NO autorizó esta acción, así que no se ejecutó."


@dataclass
class ToolRun:
    call: ToolCall  # lo que pidió el modelo
    status: Literal["ok", "error", "rechazada"]
    result: str  # lo que recibe el modelo como resultado


@dataclass
class AssistantAnswer:
    text: str
    tool_runs: list[ToolRun]
    messages: list[Message]  # lo que recibió el LLM en la última llamada
    responses: list[LLMResponse]  # una llamada (sin tools) o dos (pedir + responder)


class CourseAssistant:
    def __init__(self, llm: LLMClient, tools: list[Tool], confirm: ConfirmFn):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Hay dos tools con el mismo nombre.")
        self.confirm = confirm

    async def answer(self, question: str, history: list[Message] | None = None) -> AssistantAnswer:
        messages = build_messages(history or [], question)
        definitions = [tool.definition() for tool in self.tools.values()]

        # 1. Pedir: el modelo decide si responde directamente o pide tools.
        first = self.llm.chat(messages, tools=definitions)
        if not first.tool_calls:
            return AssistantAnswer(text=first.text, tool_runs=[], messages=messages, responses=[first])

        # 2. Ejecutar.
        tool_runs: list[ToolRun] = []
        # TODO 2: el LLM no recuerda que pidió tools: hay que devolverle su petición y los resultados.
        #   a. Agrega a messages el turno del asistente con sus tool_calls: first.as_message().
        #   b. Para cada call de first.tool_calls:
        #        - ejecútala con self.execute(call) (recuerda await) y guarda el ToolRun en tool_runs;
        #        - agrega a messages un mensaje con rol "tool":
        #            {"role": "tool", "tool_call_id": <id de la call>, "content": <resultado del run>}
        #   ¿Por qué cada resultado debe citar el id de su petición?
        raise NotImplementedError("Completa el paso 2 de CourseAssistant.answer")

        # 3. Responder: una sola ronda; el modelo ya no puede pedir más tools.
        final = self.llm.chat(messages, tools=definitions, allow_tools=False)
        return AssistantAnswer(text=final.text, tool_runs=tool_runs, messages=messages, responses=[first, final])

    async def execute(self, call: ToolCall) -> ToolRun:
        """Ejecuta una tool pedida por el modelo. Los errores se devuelven al modelo como texto."""
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolRun(call, "error", f"Error: no existe la tool {call.name!r}.")
        try:
            arguments = call.arguments()
        except ValueError as exc:
            return ToolRun(call, "error", f"Error: {exc}")

        # TODO 5: generar no es ejecutar. Si la tool NO es de solo lectura (tool.read_only es False),
        #   pide autorización con await self.confirm(tool, arguments). Si el usuario no la autoriza,
        #   no la ejecutes: devuelve ToolRun(call, "rechazada", REJECTED).
        #   Antes de completarlo, pide un supletorio en el chatbot: ¿alguien te preguntó algo?

        try:
            result = await tool.run(arguments)
        except Exception as exc:  # argumentos inválidos, regla de negocio, servidor caído...
            return ToolRun(call, "error", f"Error: {exc}")
        return ToolRun(call, "ok", result)

