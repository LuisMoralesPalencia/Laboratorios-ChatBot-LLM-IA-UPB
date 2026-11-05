"""Prueba de vida para video-notas, independiente de Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
from pathlib import Path
from typing import Callable, Iterator, Protocol
from uuid import uuid4

import cv2
import numpy as np

from config import load_face_settings, load_liveness_settings
from faces import EXPECTED_EMBEDDING_DIMENSION, FaceEngine, cosine_similarity
from sessions import Session, SessionError, SessionManager, TelegramUserId

Clock = Callable[[], datetime]
RandomSource = Callable[[list[str]], str]


class VideoCaptureLike(Protocol):
    def isOpened(self) -> bool: ...
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def get(self, property_id: int) -> float: ...
    def release(self) -> None: ...


CHALLENGES = (
    "girar_izquierda",
    "girar_derecha",
    "acercar_rostro",
)


@dataclass(frozen=True)
class LivenessChallenge:
    challenge_id: str
    telegram_user_id: str
    action: str
    issued_at: datetime
    expires_at: datetime


class ChallengeError(ValueError):
    """Reto inválido, vencido, ajeno o ya utilizado."""


class ChallengeManager:
    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        clock: Clock | None = None,
        random_source: RandomSource | None = None,
        id_source: Callable[[], str] | None = None,
    ):
        settings = load_liveness_settings()
        timeout = timeout_seconds or settings.timeout_seconds
        if timeout <= 0:
            raise ValueError("El tiempo límite del reto debe ser mayor que 0.")
        self.timeout_seconds = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.random_source = random_source or (lambda values: secrets.choice(values))
        self.id_source = id_source or (lambda: uuid4().hex)
        self._challenges: dict[str, LivenessChallenge] = {}
        self._used: set[str] = set()

    def _now(self) -> datetime:
        current = self.clock()
        return (
            current.replace(tzinfo=timezone.utc)
            if current.tzinfo is None
            else current.astimezone(timezone.utc)
        )

    def issue(self, telegram_user_id: TelegramUserId) -> LivenessChallenge:
        issued_at = self._now()
        challenge = LivenessChallenge(
            challenge_id=self.id_source(),
            telegram_user_id=str(telegram_user_id).strip(),
            action=self.random_source(list(CHALLENGES)),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=self.timeout_seconds),
        )
        self._challenges[challenge.challenge_id] = challenge
        return challenge

    def consume(self, challenge_id: str, telegram_user_id: TelegramUserId) -> LivenessChallenge:
        challenge = self._challenges.get(challenge_id)
        normalized_id = str(telegram_user_id).strip()
        if challenge is None or challenge.challenge_id in self._used:
            raise ChallengeError("reto_invalido")
        if challenge.telegram_user_id != normalized_id:
            raise ChallengeError("reto_no_corresponde")
        if self._now() >= challenge.expires_at:
            self._used.add(challenge.challenge_id)
            raise ChallengeError("reto_vencido")
        self._used.add(challenge.challenge_id)
        return challenge


@dataclass(frozen=True)
class VideoNoteMetadata:
    is_video_note: bool
    is_forwarded: bool
    telegram_user_id: TelegramUserId
    video_path: Path


@dataclass(frozen=True)
class FrameObservation:
    index: int
    similarity: float
    continuity_similarity: float
    yaw_position: float
    face_area: float
    frontal_score: float


@dataclass(frozen=True)
class LivenessResult:
    accepted: bool
    public_reason: str
    technical_reason: str
    session: Session | None = None
    challenge: LivenessChallenge | None = None
    analyzed_frames: int = 0
    similarity: float | None = None


class LivenessService:
    def __init__(
        self,
        *,
        engine: FaceEngine,
        sessions: SessionManager,
        challenges: ChallengeManager,
        enrollment_dir: Path,
        threshold: float | None = None,
        frame_step: int | None = None,
        capture_factory: Callable[[str], VideoCaptureLike] | None = None,
    ):
        settings = load_liveness_settings()
        self.engine = engine
        self.sessions = sessions
        self.challenges = challenges
        self.enrollment_dir = Path(enrollment_dir)
        self.threshold = (
            load_face_settings().match_threshold if threshold is None else threshold
        )
        self.frame_step = frame_step or settings.frame_step
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.min_frames = settings.min_frames
        self.turn_threshold = settings.turn_threshold
        self.approach_ratio = settings.approach_ratio
        self.continuity_threshold = settings.continuity_threshold

    def _failure(
        self,
        telegram_user_id: TelegramUserId,
        *,
        reason: str,
        challenge: LivenessChallenge | None = None,
        similarity: float | None = None,
        analyzed_frames: int = 0,
    ) -> LivenessResult:
        self.sessions.record_failed_login(
            telegram_user_id, reason=reason, similarity=similarity
        )
        return LivenessResult(
            False,
            "no_se_pudo_verificar",
            reason,
            challenge=challenge,
            analyzed_frames=analyzed_frames,
            similarity=similarity,
        )

    def _enrollment(self, telegram_user_id: TelegramUserId) -> tuple[str, np.ndarray]:
        user = self.sessions.users.resolve(telegram_user_id)
        path = self.enrollment_dir / f"{user.user_id}.npy"
        if not path.is_file():
            raise FileNotFoundError("enrolamiento_no_disponible")
        embeddings = np.load(path, allow_pickle=False)
        if embeddings.ndim != 2 or embeddings.shape[1] != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError("enrolamiento_dimension_invalida")
        return user.user_id, np.asarray(embeddings, dtype=np.float32)

    @staticmethod
    def _observation_from_face(
        index: int,
        face: np.ndarray,
        similarity: float,
        continuity_similarity: float,
        image_shape: tuple[int, ...],
    ) -> FrameObservation:
        x, y, width, height = [float(value) for value in face[:4]]
        right_eye = face[4:6]
        left_eye = face[6:8]
        nose = face[8:10]
        eye_center_x = (float(right_eye[0]) + float(left_eye[0])) / 2
        yaw_position = (float(nose[0]) - eye_center_x) / max(width, 1.0)
        eye_distance = abs(float(right_eye[0]) - float(left_eye[0]))
        frontal_score = 1.0 - min(1.0, abs(yaw_position) * 4.0)
        area_ratio = (width * height) / max(float(image_shape[0] * image_shape[1]), 1.0)
        return FrameObservation(
            index, similarity, continuity_similarity, yaw_position, area_ratio, frontal_score
        )

    def _iter_frames(self, capture: VideoCaptureLike) -> Iterator[tuple[int, np.ndarray]]:
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % self.frame_step == 0 and frame is not None:
                yield index, frame
            index += 1

    def _movement_valid(self, action: str, observations: list[FrameObservation]) -> bool:
        first = observations[0]
        last = observations[-1]
        if action == "girar_izquierda":
            return last.yaw_position <= first.yaw_position - self.turn_threshold
        if action == "girar_derecha":
            return last.yaw_position >= first.yaw_position + self.turn_threshold
        if action == "acercar_rostro":
            return last.face_area >= first.face_area * self.approach_ratio
        raise ValueError("reto_desconocido")

    def process_video_note(
        self, metadata: VideoNoteMetadata, challenge_id: str
    ) -> LivenessResult:
        try:
            challenge = self.challenges.consume(
                challenge_id, metadata.telegram_user_id
            )
        except ChallengeError as exc:
            return self._failure(metadata.telegram_user_id, reason=str(exc))
        if not metadata.is_video_note:
            return self._failure(
                metadata.telegram_user_id, reason="no_es_video_nota", challenge=challenge
            )
        if metadata.is_forwarded:
            return self._failure(
                metadata.telegram_user_id, reason="mensaje_reenviado", challenge=challenge
            )
        if not metadata.video_path.is_file():
            return self._failure(
                metadata.telegram_user_id, reason="video_invalido", challenge=challenge
            )
        try:
            _, enrolled = self._enrollment(metadata.telegram_user_id)
        except (FileNotFoundError, SessionError, ValueError) as exc:
            return self._failure(
                metadata.telegram_user_id, reason=str(exc), challenge=challenge
            )

        capture = self.capture_factory(str(metadata.video_path))
        if not capture.isOpened():
            capture.release()
            return self._failure(
                metadata.telegram_user_id, reason="video_invalido", challenge=challenge
            )
        observations: list[FrameObservation] = []
        previous_embedding: np.ndarray | None = None
        try:
            for index, frame in self._iter_frames(capture):
                try:
                    embedding, detection = self.engine.embedding_from_image(frame)
                except (ValueError, RuntimeError) as exc:
                    return self._failure(
                        metadata.telegram_user_id,
                        reason=f"frame_invalido:{type(exc).__name__}",
                        challenge=challenge,
                        analyzed_frames=len(observations),
                    )
                similarity = self.engine.max_similarity(embedding, enrolled)
                if similarity < self.threshold:
                    return self._failure(
                        metadata.telegram_user_id,
                        reason="identidad_no_coincide",
                        challenge=challenge,
                        similarity=similarity,
                        analyzed_frames=len(observations),
                    )
                continuity = (
                    1.0
                    if previous_embedding is None
                    else cosine_similarity(embedding, previous_embedding)
                )
                if continuity < self.continuity_threshold:
                    return self._failure(
                        metadata.telegram_user_id,
                        reason="continuidad_no_coincide",
                        challenge=challenge,
                        similarity=similarity,
                        analyzed_frames=len(observations),
                    )
                previous_embedding = embedding
                assert detection.face is not None
                observations.append(
                    self._observation_from_face(
                        index,
                        detection.face,
                        similarity,
                        continuity,
                        frame.shape,
                    )
                )
        finally:
            capture.release()

        if len(observations) < self.min_frames:
            return self._failure(
                metadata.telegram_user_id, reason="video_insuficiente", challenge=challenge,
                analyzed_frames=len(observations)
            )
        if not self._movement_valid(challenge.action, observations):
            return self._failure(
                metadata.telegram_user_id, reason="movimiento_no_corresponde",
                challenge=challenge, analyzed_frames=len(observations)
            )
        frontal = max(observations, key=lambda observation: observation.frontal_score)
        if frontal.similarity < self.threshold:
            return self._failure(
                metadata.telegram_user_id, reason="frame_frontal_no_verificado",
                challenge=challenge, similarity=frontal.similarity,
                analyzed_frames=len(observations)
            )
        try:
            session = self.sessions.create_authenticated_session(
                metadata.telegram_user_id, similarity=frontal.similarity
            )
        except SessionError:
            return LivenessResult(
                False, "no_se_pudo_verificar", "sesion_no_creada",
                challenge=challenge, analyzed_frames=len(observations),
                similarity=frontal.similarity,
            )
        return LivenessResult(
            True, "verificacion_exitosa", "ok", session=session,
            challenge=challenge, analyzed_frames=len(observations),
            similarity=frontal.similarity,
        )

    def process_temporary_video_note(
        self, metadata: VideoNoteMetadata, challenge_id: str
    ) -> LivenessResult:
        """Procesa y elimina el archivo temporal descargado por el futuro handler."""
        try:
            return self.process_video_note(metadata, challenge_id)
        finally:
            metadata.video_path.unlink(missing_ok=True)
