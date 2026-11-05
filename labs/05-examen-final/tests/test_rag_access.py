import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from documents import Document, chunk_documents, load_documents  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from rag import CourseRAG  # noqa: E402
from vector_store import VectorStore  # noqa: E402


class FakeEmbedder:
    def embed_query(self, text: str) -> np.ndarray:
        return np.array([1.0], dtype=np.float32)


class RecordingLLM:
    def __init__(self) -> None:
        self.messages = []

    def chat(self, messages):
        self.messages.append(messages)
        return LLMResponse("respuesta simulada", "fake", "stop", 0, 0)


def build_rag() -> tuple[CourseRAG, RecordingLLM, list[Document]]:
    documents = load_documents(LAB_DIR / "data")
    chunks = chunk_documents(documents, chunk_size=80, overlap=20)
    store = VectorStore("fake")
    store.add(chunks, np.ones((len(chunks), 1), dtype=np.float32))
    llm = RecordingLLM()
    rag = CourseRAG(llm, FakeEmbedder(), store, top_k=len(chunks), min_score=0.0)
    return rag, llm, documents


class RagAccessTests(unittest.TestCase):
    def test_ingestion_loads_all_levels(self):
        _, _, documents = build_rag()
        self.assertEqual({document.access_level for document in documents}, {"publico", "estudiante", "docente"})
        self.assertEqual(len(documents), 9)

    def test_public_role_receives_only_public_context(self):
        rag, llm, _ = build_rag()
        answer = rag.answer("contenido restringido", role="publico")
        context = answer.messages[-1]["content"]
        self.assertTrue(all(result.chunk.access_level == "publico" for result in answer.sources))
        self.assertNotIn("casos-especiales.md", context)
        self.assertNotIn("guia-segundo-parcial.md", context)
        self.assertEqual(llm.messages[-1], answer.messages)

    def test_student_role_receives_public_and_student_context(self):
        rag, _, _ = build_rag()
        answer = rag.answer("contenido restringido", role="estudiante")
        levels = {result.chunk.access_level for result in answer.sources}
        context = answer.messages[-1]["content"]
        self.assertEqual(levels, {"publico", "estudiante"})
        self.assertNotIn("casos-especiales.md", context)
        self.assertNotIn("rubrica-interna-proyecto.md", context)

    def test_teacher_role_receives_all_levels(self):
        rag, _, _ = build_rag()
        answer = rag.answer("casos especiales", role="docente")
        levels = {result.chunk.access_level for result in answer.sources}
        context = answer.messages[-1]["content"]
        self.assertEqual(levels, {"publico", "estudiante", "docente"})
        self.assertIn("casos-especiales.md", context)

    def test_unknown_role_is_rejected(self):
        rag, _, _ = build_rag()
        with self.assertRaises(ValueError):
            rag.retrieve("pregunta", "administrador")

    def test_restricted_text_never_reaches_unauthorized_context(self):
        rag, llm, _ = build_rag()
        for role, forbidden in (
            ("publico", ("guia-segundo-parcial.md", "casos-especiales.md")),
            ("estudiante", ("casos-especiales.md", "rubrica-interna-proyecto.md")),
        ):
            answer = rag.answer("respuesta solo docente", role=role)
            context = llm.messages[-1][-1]["content"]
            for source in forbidden:
                self.assertNotIn(source, context)

    def test_index_round_trip_preserves_metadata_and_rejects_duplicates(self):
        rag, _, _ = build_rag()
        with tempfile.TemporaryDirectory() as directory:
            rag.store.save(Path(directory))
            loaded = VectorStore.load(Path(directory))
            self.assertEqual(len(loaded.chunks), len(rag.store.chunks))
            self.assertEqual(
                [chunk.access_level for chunk in loaded.chunks],
                [chunk.access_level for chunk in rag.store.chunks],
            )
            with self.assertRaises(ValueError):
                loaded.add(loaded.chunks[:1], np.ones((1, 1), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
