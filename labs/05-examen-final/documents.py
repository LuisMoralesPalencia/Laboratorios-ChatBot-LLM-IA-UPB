"""Carga de documentos y chunking.

Un documento completo es demasiado largo y mezcla muchos temas. Lo dividimos en
fragmentos (chunks) que se indexan y se recuperan por separado: al LLM solo le
llegan los fragmentos relevantes para la pregunta.

Uso (prueba de chunking, sin modelos ni API):
    uv run python labs/04-agent-orchestration/documents.py
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from access import AccessLevel

ACCESS_PATHS: Final[dict[tuple[str, ...], AccessLevel]] = {
    ("publico",): "publico",
    ("restringido", "estudiante"): "estudiante",
    ("restringido", "docente"): "docente",
}

@dataclass(frozen=True)
class Document:
    source: str
    text: str
    access_level: AccessLevel


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    text: str
    access_level: AccessLevel


def load_documents(data_dir: Path) -> list[Document]:
    documents = []
    for path in sorted(data_dir.rglob("*.md")):
        relative_parts = path.relative_to(data_dir).parts
        access_level = next(
            (level for prefix, level in ACCESS_PATHS.items() if relative_parts[: len(prefix)] == prefix),
            None,
        )
        if access_level is None:
            raise ValueError(f"Documento fuera de una carpeta de acceso reconocida: {path}")
        documents.append(
            Document(
                source=path.relative_to(data_dir).as_posix(),
                text=path.read_text(encoding="utf-8"),
                access_level=access_level,
            )
        )
    return documents


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Divide el texto en ventanas de `chunk_size` palabras.

    Ventanas consecutivas comparten `overlap` palabras, para no cortar una idea
    justo en el borde entre dos fragmentos.
    """
    words = text.split()
    step = chunk_size - overlap
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + chunk_size]))
        if start + chunk_size >= len(words):
            break
    return chunks


def chunk_documents(documents: list[Document], chunk_size: int, overlap: int) -> list[Chunk]:
    return [
        Chunk(id=f"{doc.source}#{i}", source=doc.source, text=text, access_level=doc.access_level)
        for doc in documents
        for i, text in enumerate(chunk_text(doc.text, chunk_size, overlap))
    ]


if __name__ == "__main__":
    sample = "uno dos tres cuatro cinco seis siete ocho nueve diez"
    for chunk in chunk_text(sample, chunk_size=4, overlap=1):
        print(chunk)
    # Esperado:
    # uno dos tres cuatro
    # cuatro cinco seis siete
    # siete ocho nueve diez
