"""Calibración reproducible de FAR/FRR con muestras separadas de enrolamiento."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import sys

import numpy as np

from config import LAB_DIR
from faces import FaceEngine, model_paths


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class Pair:
    label: str
    owner: str
    reference_paths: list[str]
    probe: str
    similarity: float


def image_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"No existe el directorio de imágenes: {directory}")
    return sorted(path for path in directory.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def reference_embeddings(engine: FaceEngine, paths: list[Path]) -> np.ndarray:
    return np.asarray([engine.embedding_from_path(path)[0] for path in paths], dtype=np.float32)


def build_pairs(
    engine: FaceEngine,
    references: dict[str, tuple[list[Path], np.ndarray]],
    validations: dict[str, list[Path]],
    impostors: list[Path],
) -> list[Pair]:
    pairs: list[Pair] = []
    for owner, (_, enrolled) in references.items():
        validation_paths = validations[owner]
        for probe_path in validation_paths:
            probe, _ = engine.embedding_from_path(probe_path)
            score = engine.max_similarity(probe, enrolled)
            pairs.append(
                Pair("genuine", owner, [str(path) for path in references[owner][0]], str(probe_path), score)
            )
    for probe_path in impostors:
        probe, _ = engine.embedding_from_path(probe_path)
        for owner, (_, enrolled) in references.items():
            score = engine.max_similarity(probe, enrolled)
            pairs.append(
                Pair("impostor", owner, [str(path) for path in references[owner][0]], str(probe_path), score)
            )
    return pairs


def far_frr(pairs: list[Pair], threshold: float) -> dict[str, float | int]:
    genuine = [pair for pair in pairs if pair.label == "genuine"]
    impostor = [pair for pair in pairs if pair.label == "impostor"]
    false_rejections = sum(pair.similarity < threshold for pair in genuine)
    false_acceptances = sum(pair.similarity >= threshold for pair in impostor)
    return {
        "threshold": threshold,
        "genuine_pairs": len(genuine),
        "impostor_pairs": len(impostor),
        "false_rejections": false_rejections,
        "false_acceptances": false_acceptances,
        "frr": false_rejections / len(genuine) if genuine else 0.0,
        "far": false_acceptances / len(impostor) if impostor else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibra FAR/FRR sin modificar fotografías.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--owner-a", required=True)
    parser.add_argument("--owner-b", required=True)
    parser.add_argument("--thresholds", default="0.363,0.40,0.45")
    parser.add_argument("--manifest", type=Path, default=LAB_DIR / "calibracion_manifest.json")
    parser.add_argument("--table", type=Path, default=LAB_DIR / "calibracion_far_frr.csv")
    args = parser.parse_args(argv)

    root = args.dataset_dir
    directories = {
        args.owner_a: (root / "data_enrolamiento_luis", root / "dataset_validacion_luis"),
        args.owner_b: (root / "dataset_enrolamiento_jhostyn", root / "dataset_validacion_jhostyn"),
    }
    references = {}
    validations = {}
    for owner, (enrollment_dir, validation_dir) in directories.items():
        enrollment = image_paths(enrollment_dir)
        validation = image_paths(validation_dir)
        if not 3 <= len(enrollment) <= 5:
            raise ValueError(f"{owner}: se requieren 3–5 fotos de enrolamiento, hay {len(enrollment)}.")
        if len(validation) < 5:
            raise ValueError(f"{owner}: se requieren al menos 5 fotos genuinas, hay {len(validation)}.")
        references[owner] = (enrollment, None)
        validations[owner] = validation[:5]
    impostors = image_paths(root / "dataset_impostores")
    if len(impostors) < 20:
        raise ValueError(f"Se requieren al menos 20 impostores, hay {len(impostors)}.")
    engine = FaceEngine(*model_paths(LAB_DIR))
    references = {
        owner: (paths, reference_embeddings(engine, paths))
        for owner, (paths, _) in references.items()
    }
    pairs = build_pairs(engine, references, validations, impostors[:20])
    if len([pair for pair in pairs if pair.label == "genuine"]) < 10:
        raise ValueError("No se construyeron al menos 10 pares genuinos.")
    if len([pair for pair in pairs if pair.label == "impostor"]) < 20:
        raise ValueError("No se construyeron al menos 20 pares impostores.")
    thresholds = [float(value.strip()) for value in args.thresholds.split(",") if value.strip()]
    if len(thresholds) < 3 or 0.363 not in thresholds:
        raise ValueError("Se requieren al menos tres umbrales, incluyendo 0.363.")
    args.manifest.write_text(json.dumps([asdict(pair) for pair in pairs], ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [far_frr(pairs, threshold) for threshold in thresholds]
    with args.table.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
