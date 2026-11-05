import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import numpy as np

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from audit import AuditLogger  # noqa: E402
from liveness import (  # noqa: E402
    CHALLENGES,
    ChallengeError,
    ChallengeManager,
    FrameObservation,
    LivenessService,
    VideoNoteMetadata,
)
from faces import FaceDetection  # noqa: E402
from sessions import SessionManager, UserRepository  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class FakeCapture:
    def __init__(self, frames: list[np.ndarray], opened: bool = True):
        self.frames = frames
        self.opened = opened
        self.released = False

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def get(self, property_id: int) -> float:
        return 0.0

    def release(self) -> None:
        self.released = True


class LivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        users = root / "usuarios.json"
        users.write_text(json.dumps({"usuarios": [{
            "id": "student", "nombre": "Estudiante", "rol": "estudiante",
            "codigo_estudiante": "S-1", "telegram_user_id": 101,
        }]}), encoding="utf-8")
        self.clock = FakeClock()
        self.audit_path = root / "audit.jsonl"
        self.sessions = SessionManager(
            UserRepository.from_json(users), AuditLogger(self.audit_path),
            session_minutes=10, login_max_attempts=3, login_block_minutes=15,
            clock=self.clock,
        )
        self.challenges = ChallengeManager(
            timeout_seconds=60, clock=self.clock,
            random_source=lambda values: values[0], id_source=lambda: "challenge-1",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_issue_associates_user_and_uses_allowed_challenges(self):
        challenge = self.challenges.issue(101)
        self.assertEqual(challenge.telegram_user_id, "101")
        self.assertIn(challenge.action, CHALLENGES)

    def test_expiration_and_single_use(self):
        challenge = self.challenges.issue(101)
        self.clock.advance(seconds=60)
        with self.assertRaises(ChallengeError):
            self.challenges.consume(challenge.challenge_id, 101)
        with self.assertRaises(ChallengeError):
            self.challenges.consume(challenge.challenge_id, 101)

    def test_rejects_forwarded_photo_and_invalid_video(self):
        engine = Mock()
        service = LivenessService(
            engine=engine, sessions=self.sessions, challenges=self.challenges,
            enrollment_dir=Path(self.temp.name),
            capture_factory=lambda _: FakeCapture([]),
        )
        challenge = self.challenges.issue(101)
        result = service.process_video_note(
            VideoNoteMetadata(False, False, 101, Path("missing")), challenge.challenge_id
        )
        self.assertFalse(result.accepted)
        challenge = self.challenges.issue(101)
        result = service.process_video_note(
            VideoNoteMetadata(True, True, 101, Path("missing")), challenge.challenge_id
        )
        self.assertFalse(result.accepted)

    def test_movement_rules_are_specific(self):
        service = LivenessService.__new__(LivenessService)
        service.turn_threshold = 0.08
        service.approach_ratio = 1.25
        left = [FrameObservation(0, .8, 1, .2, .1, .2), FrameObservation(1, .8, 1, .1, .1, .8)]
        right = [FrameObservation(0, .8, 1, -.2, .1, .2), FrameObservation(1, .8, 1, -.1, .1, .8)]
        close = [FrameObservation(0, .8, 1, 0, .1, 1), FrameObservation(1, .8, 1, 0, .14, 1)]
        self.assertTrue(service._movement_valid("girar_izquierda", left))
        self.assertTrue(service._movement_valid("girar_derecha", right))
        self.assertTrue(service._movement_valid("acercar_rostro", close))
        self.assertFalse(service._movement_valid("girar_derecha", left))

    def test_no_biometric_data_in_audit(self):
        challenge = self.challenges.issue(101)
        service = LivenessService(
            engine=Mock(), sessions=self.sessions, challenges=self.challenges,
            enrollment_dir=Path(self.temp.name),
        )
        service.process_video_note(
            VideoNoteMetadata(False, False, 101, Path("missing")), challenge.challenge_id
        )
        content = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("embedding", content.lower())
        self.assertNotIn("frame", content.lower())

    def test_success_creates_one_session_after_all_checks(self):
        enrollment_dir = Path(self.temp.name) / "rostros"
        enrollment_dir.mkdir()
        np.save(enrollment_dir / "student.npy", np.ones((2, 128), dtype=np.float32), allow_pickle=False)
        video = Path(self.temp.name) / "video.mp4"
        video.write_bytes(b"video")
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(3)]
        faces = [
            np.array([20, 20, 40, 40, 25, 35, 55, 35, 48, 40, 30, 55, 50, 55, 1], dtype=np.float32),
            np.array([20, 20, 40, 40, 25, 35, 55, 35, 42, 40, 30, 55, 50, 55, 1], dtype=np.float32),
            np.array([20, 20, 40, 40, 25, 35, 55, 35, 36, 40, 30, 55, 50, 55, 1], dtype=np.float32),
        ]
        engine = Mock()
        engine.embedding_from_image.side_effect = [
            (np.ones(128, dtype=np.float32), FaceDetection(1, face)) for face in faces
        ]
        engine.max_similarity.return_value = 0.9
        service = LivenessService(
            engine=engine, sessions=self.sessions,
            challenges=ChallengeManager(
                timeout_seconds=60, clock=self.clock,
                random_source=lambda values: "girar_izquierda", id_source=lambda: "success",
            ),
            enrollment_dir=enrollment_dir, frame_step=1,
            capture_factory=lambda _: FakeCapture(frames.copy()),
        )
        # Use the challenge manager attached to the service so the issued challenge is valid.
        challenge = service.challenges.issue(101)
        result = service.process_video_note(
            VideoNoteMetadata(True, False, 101, video), challenge.challenge_id
        )
        self.assertTrue(result.accepted)
        self.assertIsNotNone(self.sessions.get_active_session(101))

    def test_multiple_faces_cannot_create_session(self):
        enrollment_dir = Path(self.temp.name) / "rostros"
        enrollment_dir.mkdir()
        np.save(enrollment_dir / "student.npy", np.ones((2, 128), dtype=np.float32), allow_pickle=False)
        video = Path(self.temp.name) / "video.mp4"
        video.write_bytes(b"video")
        engine = Mock()
        engine.embedding_from_image.side_effect = ValueError("se requiere exactamente un rostro")
        service = LivenessService(
            engine=engine, sessions=self.sessions, challenges=self.challenges,
            enrollment_dir=enrollment_dir, frame_step=1,
            capture_factory=lambda _: FakeCapture([np.zeros((100, 100, 3), dtype=np.uint8)]),
        )
        challenge = self.challenges.issue(101)
        result = service.process_video_note(
            VideoNoteMetadata(True, False, 101, video), challenge.challenge_id
        )
        self.assertFalse(result.accepted)
        self.assertIsNone(self.sessions.get_active_session(101))

    def test_temporary_video_is_removed_after_processing(self):
        video = Path(self.temp.name) / "temporary.mp4"
        video.write_bytes(b"video")
        challenge = self.challenges.issue(101)
        service = LivenessService(
            engine=Mock(), sessions=self.sessions, challenges=self.challenges,
            enrollment_dir=Path(self.temp.name),
        )
        service.process_temporary_video_note(
            VideoNoteMetadata(False, False, 101, video), challenge.challenge_id
        )
        self.assertFalse(video.exists())


if __name__ == "__main__":
    unittest.main()
