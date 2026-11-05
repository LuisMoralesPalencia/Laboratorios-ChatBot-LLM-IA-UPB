"""Reconocimiento facial 1:1 con YuNet y SFace ejecutados localmente."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import load_face_settings

EXPECTED_EMBEDDING_DIMENSION = 128


class FaceModelError(RuntimeError):
    """Error al cargar o utilizar un modelo facial."""


@dataclass(frozen=True)
class FaceDetection:
    count: int
    face: np.ndarray | None
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.count == 1


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    face_count: int
    similarity: float | None
    reason: str


def cosine_similarity(first: np.ndarray, second: np.ndarray) -> float:
    first_vector = np.asarray(first, dtype=np.float32).reshape(-1)
    second_vector = np.asarray(second, dtype=np.float32).reshape(-1)
    if first_vector.shape != second_vector.shape or first_vector.size == 0:
        raise ValueError("Los embeddings deben tener la misma dimensión y no estar vacíos.")
    denominator = float(np.linalg.norm(first_vector) * np.linalg.norm(second_vector))
    if denominator <= 1e-12:
        raise ValueError("No se puede calcular similitud con un embedding nulo.")
    return float(np.dot(first_vector, second_vector) / denominator)


class FaceEngine:
    def __init__(
        self,
        detector_model: Path,
        recognizer_model: Path,
        *,
        detector_input_size: tuple[int, int] = (320, 320),
        score_threshold: float = 0.7,
        nms_threshold: float = 0.3,
        top_k: int = 5000,
    ):
        self.detector_model = Path(detector_model)
        self.recognizer_model = Path(recognizer_model)
        for model_path in (self.detector_model, self.recognizer_model):
            if not model_path.is_file():
                raise FileNotFoundError(f"No existe el modelo facial: {model_path}")
        if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
            raise FaceModelError("La instalación de OpenCV no expone FaceDetectorYN y FaceRecognizerSF.")
        try:
            self.detector = cv2.FaceDetectorYN.create(
                str(self.detector_model),
                "",
                detector_input_size,
                score_threshold,
                nms_threshold,
                top_k,
            )
            self.recognizer = cv2.FaceRecognizerSF.create(str(self.recognizer_model), "")
        except cv2.error as exc:
            raise FaceModelError(f"No se pudieron cargar los modelos faciales: {exc}") from exc

    @staticmethod
    def read_image(path: Path) -> np.ndarray:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
            raise ValueError(f"No se pudo abrir una imagen válida: {path}")
        return image

    def detect(self, image: np.ndarray) -> FaceDetection:
        if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
            raise ValueError("La imagen no tiene dimensiones válidas.")
        self.detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.detector.detect(image)
        rows = 0 if faces is None else int(faces.shape[0])
        if rows != 1:
            return FaceDetection(rows, None, "se requiere exactamente un rostro")
        return FaceDetection(rows, np.asarray(faces[0], dtype=np.float32))

    def embedding_from_image(self, image: np.ndarray) -> tuple[np.ndarray, FaceDetection]:
        detection = self.detect(image)
        if not detection.accepted or detection.face is None:
            raise ValueError(detection.reason or "La imagen no contiene exactamente un rostro.")
        try:
            aligned = self.recognizer.alignCrop(image, detection.face)
            embedding = np.asarray(self.recognizer.feature(aligned), dtype=np.float32).reshape(-1)
        except cv2.error as exc:
            raise FaceModelError(f"No se pudo extraer el embedding: {exc}") from exc
        if embedding.size != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"Dimensión de embedding inesperada: {embedding.size}; "
                f"se esperaba {EXPECTED_EMBEDDING_DIMENSION}."
            )
        return embedding, detection

    def embedding_from_path(self, path: Path) -> tuple[np.ndarray, FaceDetection]:
        return self.embedding_from_image(self.read_image(path))

    @staticmethod
    def max_similarity(probe: np.ndarray, enrolled_embeddings: np.ndarray) -> float:
        enrolled = np.asarray(enrolled_embeddings, dtype=np.float32)
        if enrolled.ndim != 2 or enrolled.shape[1] != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError("Los embeddings enrolados deben tener forma (n, 128).")
        return max(cosine_similarity(probe, reference) for reference in enrolled)

    def verify_embedding(
        self, image: np.ndarray, enrolled_embeddings: np.ndarray, threshold: float | None = None
    ) -> VerificationResult:
        try:
            probe, detection = self.embedding_from_image(image)
        except ValueError as exc:
            count = 0 if "exactamente un rostro" in str(exc) else -1
            return VerificationResult(False, count, None, str(exc))
        effective_threshold = (
            load_face_settings().match_threshold if threshold is None else threshold
        )
        similarity = self.max_similarity(probe, enrolled_embeddings)
        return VerificationResult(
            similarity >= effective_threshold,
            detection.count,
            similarity,
            (
                "similitud_supera_umbral"
                if similarity >= effective_threshold
                else "similitud_bajo_umbral"
            ),
        )


def model_paths(lab_dir: Path) -> tuple[Path, Path]:
    models_dir = lab_dir / "modelos"
    return (
        models_dir / "face_detection_yunet" / "face_detection_yunet_2023mar.onnx",
        models_dir / "face_recognition_sface" / "face_recognition_sface_2021dec.onnx",
    )
