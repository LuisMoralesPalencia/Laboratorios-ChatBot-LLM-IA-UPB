import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from access import can_access, effective_role  # noqa: E402
from audit import AuditLogger  # noqa: E402
from sessions import SessionError, SessionManager, UserRepository  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        users_path = root / "usuarios.json"
        users_path.write_text(
            json.dumps(
                {
                    "usuarios": [
                        {
                            "id": "student",
                            "nombre": "Estudiante",
                            "rol": "estudiante",
                            "codigo_estudiante": "S-1",
                            "telegram_user_id": 101,
                        },
                        {
                            "id": "teacher",
                            "nombre": "Docente",
                            "rol": "docente",
                            "codigo_estudiante": None,
                            "telegram_user_id": 202,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.audit_path = root / "audit.jsonl"
        self.audit = AuditLogger(self.audit_path)
        self.clock = FakeClock()
        self.manager = SessionManager(
            UserRepository.from_json(users_path),
            self.audit,
            session_minutes=10,
            login_max_attempts=3,
            login_block_minutes=15,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def audit_entries(self) -> list[dict]:
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]

    def test_resolves_user_and_rejects_unknown_id(self):
        user = self.manager.users.resolve(101)
        self.assertEqual((user.name, user.role, user.student_code), ("Estudiante", "estudiante", "S-1"))
        with self.assertRaises(SessionError):
            self.manager.users.resolve(999)
        with self.assertRaises(SessionError):
            self.manager.create_authenticated_session(999)
        self.assertEqual(self.audit_entries()[-1]["reason"], "telegram_id_desconocido")

    def test_creates_and_recovers_only_the_correct_session(self):
        session = self.manager.create_authenticated_session(101, similarity=0.91)
        self.assertTrue(session.session_id)
        self.assertNotEqual(session.session_id, session.telegram_user_id)
        self.assertEqual(self.manager.get_active_session(101), session)
        self.assertIsNone(self.manager.get_active_session(202))
        self.assertEqual(effective_role(session, self.clock()), "estudiante")

    def test_session_id_is_stable_active_and_unique_per_login(self):
        first = self.manager.create_authenticated_session(101)
        self.assertEqual(self.manager.get_active_session(101).session_id, first.session_id)
        second = self.manager.create_authenticated_session(101)
        teacher = self.manager.create_authenticated_session(202)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertNotEqual(first.session_id, teacher.session_id)
        self.assertNotEqual(second.session_id, teacher.session_id)
        self.assertNotEqual(first.session_id, first.telegram_user_id)
        self.assertNotEqual(teacher.session_id, teacher.telegram_user_id)

    def test_logout_and_expiration_remove_session_with_its_identifier(self):
        session = self.manager.create_authenticated_session(101)
        self.manager.logout(101)
        self.assertIsNone(self.manager.get_active_session(101))
        replacement = self.manager.create_authenticated_session(101)
        self.assertNotEqual(session.session_id, replacement.session_id)
        self.clock.advance(minutes=10)
        self.assertIsNone(self.manager.get_active_session(101))

    def test_expiration_and_logout_return_public_access(self):
        session = self.manager.create_authenticated_session(101)
        self.clock.advance(minutes=10)
        self.assertIsNone(self.manager.get_active_session(101))
        self.assertEqual(effective_role(session, self.clock()), "publico")
        self.assertFalse(can_access(effective_role(session, self.clock()), "estudiante"))
        self.manager.create_authenticated_session(101)
        self.manager.logout(101)
        self.assertIsNone(self.manager.get_active_session(101))
        self.assertEqual(effective_role(None, self.clock()), "publico")
        self.assertFalse(can_access("publico", "estudiante"))

    def test_three_failures_block_and_blocked_attempt_is_rejected(self):
        for _ in range(3):
            self.assertFalse(self.manager.record_failed_login(101, reason="rostro_no_coincide"))
        with self.assertRaises(SessionError):
            self.manager.create_authenticated_session(101)
        self.assertEqual(self.manager.get_active_session(101), None)
        self.assertEqual(len(self.audit_entries()), 4)
        self.assertEqual(self.audit_entries()[-1]["reason"], "cuenta_bloqueada")

    def test_success_resets_failures(self):
        self.manager.record_failed_login(101, reason="reto_invalido")
        self.manager.create_authenticated_session(101)
        self.manager.logout(101)
        self.manager.record_failed_login(101, reason="reto_invalido")
        self.assertIsNotNone(self.manager.create_authenticated_session(101))

    def test_block_expires_with_injected_clock(self):
        for _ in range(3):
            self.manager.record_failed_login(101, reason="fallo")
        self.clock.advance(minutes=15)
        self.assertIsNotNone(self.manager.create_authenticated_session(101))

    def test_audit_records_required_events_without_biometrics(self):
        self.manager.create_authenticated_session(101, similarity=0.88)
        self.manager.record_denied_access(101, reason="nivel_no_permitido")
        self.manager.record_restricted_tool(101, tool="registrar_nota")
        contents = self.audit_path.read_text(encoding="utf-8")
        entries = self.audit_entries()
        self.assertEqual(
            {entry["event"] for entry in entries},
            {"login_aceptado", "acceso_denegado", "tool_restringida_ejecutada"},
        )
        self.assertNotIn("foto", contents.lower())
        self.assertNotIn("frame", contents.lower())
        self.assertNotIn("embedding", contents.lower())
        self.assertNotIn("token", contents.lower())
        self.assertNotIn("api_key", contents.lower())

    def test_invalid_and_ambiguous_user_data_is_rejected(self):
        root = Path(self.temp_dir.name)
        for name, users in (
            ("missing.json", [{"id": "x", "nombre": "", "rol": "estudiante", "codigo_estudiante": "x"}]),
            (
                "duplicate.json",
                [
                    {"id": "x", "nombre": "X", "rol": "docente", "codigo_estudiante": None, "telegram_user_id": 1},
                    {"id": "y", "nombre": "Y", "rol": "docente", "codigo_estudiante": None, "telegram_user_id": 1},
                ],
            ),
        ):
            path = root / name
            path.write_text(json.dumps({"usuarios": users}), encoding="utf-8")
            with self.assertRaises(ValueError):
                UserRepository.from_json(path)


if __name__ == "__main__":
    unittest.main()
