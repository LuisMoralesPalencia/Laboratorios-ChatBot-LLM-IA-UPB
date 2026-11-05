"""Enrolamiento local: guarda embeddings, nunca copias de fotografías."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np

from config import DATA_DIR, LAB_DIR
from faces import EXPECTED_EMBEDDING_DIMENSION, FaceEngine, model_paths
from sessions import UserRepository

MIN_PHOTOS = 3
MAX_PHOTOS = 5


def enroll(
    *,
    user_id: str,
    telegram_user_id: str,
    image_paths: list[Path],
    users_path: Path,
    output_dir: Path,
    engine: FaceEngine,
) -> Path:
    if not MIN_PHOTOS <= len(image_paths) <= MAX_PHOTOS:
        raise ValueError(f"El enrolamiento requiere entre {MIN_PHOTOS} y {MAX_PHOTOS} fotografías.")
    user = UserRepository.from_json(users_path).resolve(telegram_user_id)
    if user.user_id != user_id:
        raise ValueError("El usuario interno no corresponde al telegram_user_id proporcionado.")

    embeddings = []
    for path in image_paths:
        embedding, _ = engine.embedding_from_path(path)
        embeddings.append(embedding)
    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.shape != (len(image_paths), EXPECTED_EMBEDDING_DIMENSION):
        raise ValueError("Los embeddings extraídos no tienen la forma esperada.")

    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / f"{user_id}.json"
    embedding_path = output_dir / f"{user_id}.npy"
    temp_embedding_path = output_dir / f".{user_id}.tmp.npy"
    temp_metadata_path = output_dir / f".{user_id}.json.tmp"
    np.save(temp_embedding_path, matrix, allow_pickle=False)
    metadata = {
        "user_id": user.user_id,
        "telegram_user_id": user.telegram_user_id,
        "embedding_dimension": EXPECTED_EMBEDDING_DIMENSION,
        "sample_count": len(image_paths),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    temp_metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_embedding_path.replace(embedding_path)
    temp_metadata_path.replace(metadata_path)
    return embedding_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrolamiento facial local.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--telegram-user-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=LAB_DIR / "rostros")
    parser.add_argument("photos", nargs="+", type=Path)
    args = parser.parse_args(argv)
    detector_model, recognizer_model = model_paths(LAB_DIR)
    engine = FaceEngine(detector_model, recognizer_model)
    path = enroll(
        user_id=args.user_id,
        telegram_user_id=args.telegram_user_id,
        image_paths=args.photos,
        users_path=DATA_DIR / "usuarios.json",
        output_dir=args.output_dir,
        engine=engine,
    )
    print(f"Enrolamiento guardado en {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
