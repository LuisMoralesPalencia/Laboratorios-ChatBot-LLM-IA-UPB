import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from calibrate import far_frr  # noqa: E402
from faces import EXPECTED_EMBEDDING_DIMENSION, FaceDetection, FaceEngine, VerificationResult, cosine_similarity  # noqa: E402


class FaceUnitTests(unittest.TestCase):
    def test_cosine_similarity(self):
        self.assertAlmostEqual(cosine_similarity(np.array([1.0, 0.0]), np.array([1.0, 0.0])), 1.0)
        self.assertAlmostEqual(cosine_similarity(np.array([1.0, 0.0]), np.array([0.0, 1.0])), 0.0)

    def test_invalid_embedding_dimension_is_rejected(self):
        with self.assertRaises(ValueError):
            cosine_similarity(np.zeros(128), np.zeros(127))

    def test_missing_model_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            FaceEngine(Path("missing-yunet.onnx"), Path("missing-sface.onnx"))

    def test_detection_result_requires_exactly_one_face(self):
        self.assertFalse(FaceDetection(0, None, "sin rostro").accepted)
        self.assertFalse(FaceDetection(2, None, "multiples").accepted)
        self.assertTrue(FaceDetection(1, np.zeros(15, dtype=np.float32)).accepted)

    def test_verification_result_shape(self):
        result = VerificationResult(True, 1, 0.9, "ok")
        self.assertEqual(result.face_count, 1)

    def test_far_frr_counts(self):
        from calibrate import Pair
        pairs = [
            Pair("genuine", "a", ["r1"], "g1", 0.8),
            Pair("genuine", "a", ["r1"], "g2", 0.2),
            Pair("impostor", "a", ["r1"], "i1", 0.7),
            Pair("impostor", "a", ["r1"], "i2", 0.1),
        ]
        result = far_frr(pairs, 0.5)
        self.assertEqual(result["false_rejections"], 1)
        self.assertEqual(result["false_acceptances"], 1)
        self.assertEqual(result["genuine_pairs"], 2)
        self.assertEqual(result["impostor_pairs"], 2)


if __name__ == "__main__":
    unittest.main()
