"""Prompts del asistente y construcción de la lista de mensajes.

Los prompts son parte del diseño de la aplicación: se versionan y se revisan
como cualquier otro código.
"""

from llm_client import Message

# TODO 4: diseña el prompt de sistema del asistente. Como mínimo debe:
#   - definir el rol (asistente del curso de IA) y el idioma de respuesta;
#   - indicar el nivel de los estudiantes (ya conocen Transformers);
#   - prohibir inventar información específica del curso (fechas, notas, programa).
SYSTEM_PROMPT = """Eres el asistente del curso de Inteligencia Artificial.
Responde siempre en español, con explicaciones claras y adecuadas para estudiantes
que ya conocen los fundamentos de los Transformers.
No inventes información específica del curso, como fechas, notas, programa o material
propio. Si una pregunta depende de esos datos y no tienes los documentos del curso,
indica claramente que no dispones de esa información."""

ANALYSIS_PROMPT = """Analiza la pregunta de un estudiante del curso de IA.
Responde ÚNICAMENTE con un objeto JSON con exactamente estas claves:
- "tema": tema principal de la pregunta, en pocas palabras.
- "dificultad": uno de "basica", "intermedia" o "avanzada".
- "requiere_documentos_del_curso": true si la respuesta depende de información específica \
del curso (fechas, notas, programa, material propio) que no es conocimiento general; false en otro caso.
- "respuesta_corta": respuesta en máximo dos frases; si requiere documentos del curso, \
escribe "No tengo esa información"."""


def build_messages(history: list[Message], user_input: str) -> list[Message]:
    """Construye lo que realmente recibe el LLM: system + historial + pregunta actual."""
    # TODO 3: devuelve una lista con, en este orden:
    #   1. el mensaje de rol "system" con SYSTEM_PROMPT;
    #   2. todos los mensajes de history;
    #   3. el mensaje de rol "user" con user_input.
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_input},
    ]
