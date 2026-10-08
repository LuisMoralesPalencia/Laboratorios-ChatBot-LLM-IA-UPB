# AI Systems Lab

Laboratorios de la unidad *De Transformer a sistemas orquestados con LLM* del curso de
Inteligencia Artificial.

Durante 4 semanas construiremos, paso a paso, el **Asistente Inteligente del Curso de IA**.
Cada semana se publica un lab nuevo que parte del anterior y agrega pocos conceptos nuevos.
El énfasis está en la **ingeniería de software** de sistemas con IA: qué responsabilidad tiene
cada componente, cómo interactúan y qué esconden los frameworks.

## Labs publicados

| Semana | Lab | Producto |
|--------|-----|----------|
| 1 | [`labs/01-llm`](labs/01-llm/) | Chatbot con LLM en Python (`Usuario → LLM`) |
| 2 | [`labs/02-rag`](labs/02-rag/) | Chatbot RAG sobre los documentos del curso (`Usuario → RAG → LLM`) |
| 3 | [`labs/03-mcp`](labs/03-mcp/) | Asistente con tools y un servidor MCP (`Usuario → Asistente → LLM + Tools`) |

Los siguientes labs se agregarán a este repositorio cada semana. Actualiza tu copia con `git pull`.

## Requisitos

- Python 3.11 o superior
- [uv](https://docs.astral.sh/uv/) (gestor de entornos y dependencias de Python)
- Una API key de un proveedor de LLM. Recomendado: [Groq](https://console.groq.com/keys) (gratuito).
  También funcionan [OpenRouter](https://openrouter.ai/keys) (gratuito),
  [OpenAI](https://platform.openai.com/api-keys) y [DeepSeek](https://platform.deepseek.com/api_keys) (de pago).

## Configuración inicial

Desde la raíz del repositorio:

```bash
uv sync                  # crea .venv e instala las dependencias
cp .env.example .env     # en Windows (PowerShell): Copy-Item .env.example .env
```

Edita `.env` y completa `LLM_PROVIDER`, `LLM_API_KEY` y `LLM_MODEL`.

> **El archivo `.env` contiene tu API key: nunca lo subas a un repositorio ni lo incluyas en una
> entrega.** Ya está en `.gitignore`. Si publicas una clave por error, revócala de inmediato en la
> consola del proveedor y crea una nueva.

### Opción: GitHub Codespaces

Si no quieres instalar nada en tu equipo, abre el repositorio en un Codespace
(**Code → Codespaces → Create codespace**) y en la terminal ejecuta:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync
```

Para la API key tienes dos opciones:

- **Recomendada:** crea un secreto `LLM_API_KEY` en GitHub (*Settings → Codespaces → Secrets*) con
  acceso a tu repositorio. No queda ningún archivo con la clave.
- Crear el `.env` dentro del Codespace, como en la configuración local.

Al terminar, detén el Codespace (botón **Codespaces** abajo a la izquierda → *Stop Current Codespace*).
Si creaste un `.env`, bórralo o elimina el Codespace desde [github.com/codespaces](https://github.com/codespaces).

## Cómo trabajar

Todos los comandos se ejecutan **desde la raíz** del repositorio:

```bash
uv run python labs/01-llm/chatbot.py
```

La guía de cada lab (PDF) se publica en la tarea de Teams correspondiente, con objetivo,
arquitectura, pasos, pruebas, preguntas de análisis y evidencias de entrega. Los puntos a
completar están marcados con `TODO` en el código.

## Estructura

```text
ai-systems-lab-students/
├── README.md
├── pyproject.toml     dependencias compartidas por todos los labs
├── uv.lock            versiones exactas de las dependencias
├── .env.example       plantilla de configuración (cópiala como .env)
└── labs/              un lab por semana
    ├── 01-llm/
    ├── 02-rag/
    └── 03-mcp/
```
