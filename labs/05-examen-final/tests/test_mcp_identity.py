import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from mcp_identity import (  # noqa: E402
    BOOTSTRAP_AUDIENCE,
    DEFAULT_ISSUER,
    IdentityError,
    ProcessIdentityContext,
    context_from_user,
    issue_bootstrap,
    issue_session_token,
    load_private_key,
    load_public_key,
    verify_bootstrap,
    verify_session_token,
)
from sessions import UserRepository  # noqa: E402


class McpIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.users = self._users()
        self.now = datetime(2026, 11, 5, 6, 45, tzinfo=timezone.utc)
        self.context = context_from_user(
            session_id="session-1",
            telegram_user_id="12345",
            jti="process-jti-1",
            users=self.users,
        )

    def _users(self) -> UserRepository:
        self.temp_dir = TemporaryDirectory()
        path = Path(self.temp_dir.name) / "usuarios.json"
        path.write_text(
            json.dumps(
                {
                    "usuarios": [
                        {
                            "id": "student",
                            "nombre": "Estudiante",
                            "rol": "estudiante",
                            "codigo_estudiante": "S-1",
                            "telegram_user_id": 12345,
                        },
                        {
                            "id": "teacher",
                            "nombre": "Docente",
                            "rol": "docente",
                            "codigo_estudiante": None,
                            "telegram_user_id": 67890,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return UserRepository.from_json(path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_and_session_tokens_validate(self):
        bootstrap = issue_bootstrap(self.context, self.private_key, now=self.now)
        session = issue_session_token(self.context, self.private_key, now=self.now)
        self.assertEqual(
            verify_bootstrap(
                bootstrap,
                self.private_key.public_key(),
                self.users,
                expected_context=self.context,
                now=self.now + timedelta(seconds=1),
            ),
            self.context,
        )
        self.assertEqual(
            verify_session_token(
                session,
                self.private_key.public_key(),
                self.users,
                expected_context=self.context,
                now=self.now + timedelta(seconds=1),
            ),
            self.context,
        )

    def test_context_resolves_role_and_code_from_repository(self):
        self.assertEqual(self.context.role, "estudiante")
        self.assertEqual(self.context.codigo, "S-1")

    def test_pem_key_loading(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            private_path = root / "private.pem"
            public_path = root / "public.pem"
            private_path.write_bytes(
                self.private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            public_path.write_bytes(
                self.private_key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            self.assertIsInstance(load_private_key(private_path), Ed25519PrivateKey)
            self.assertEqual(load_public_key(public_path), self.private_key.public_key())

    def test_tampered_signature_is_rejected(self):
        token = issue_session_token(self.context, self.private_key, now=self.now)
        tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
        with self.assertRaises(IdentityError):
            verify_session_token(tampered, self.private_key.public_key(), self.users, expected_context=self.context)

    def test_expired_token_is_rejected(self):
        token = issue_session_token(
            self.context,
            self.private_key,
            now=self.now - timedelta(minutes=2),
            lifetime_seconds=30,
        )
        with self.assertRaises(IdentityError):
            verify_session_token(
                token,
                self.private_key.public_key(),
                self.users,
                expected_context=self.context,
                now=self.now,
            )

    def test_wrong_audience_and_issuer_are_rejected(self):
        token = issue_bootstrap(self.context, self.private_key, now=self.now)
        with self.assertRaises(IdentityError):
            verify_session_token(token, self.private_key.public_key(), self.users, expected_context=self.context)
        wrong_issuer = issue_session_token(self.context, self.private_key, issuer="other-issuer", now=self.now)
        with self.assertRaises(IdentityError):
            verify_session_token(
                wrong_issuer,
                self.private_key.public_key(),
                self.users,
                expected_context=self.context,
            )

    def test_non_eddsa_algorithm_is_rejected(self):
        claims = self._claims("session", "mcp-tool-call")
        token = jwt.encode(claims, "not-an-ed25519-key-with-at-least-32-bytes", algorithm="HS256")
        with self.assertRaises(IdentityError):
            verify_session_token(token, self.private_key.public_key(), self.users, expected_context=self.context)

    def test_missing_required_claim_is_rejected(self):
        claims = self._claims("session", "mcp-tool-call")
        del claims["jti"]
        token = jwt.encode(claims, self.private_key, algorithm="EdDSA")
        with self.assertRaises(IdentityError):
            verify_session_token(token, self.private_key.public_key(), self.users, expected_context=self.context)

    def test_token_types_cannot_be_interchanged(self):
        bootstrap = issue_bootstrap(self.context, self.private_key, now=self.now)
        session = issue_session_token(self.context, self.private_key, now=self.now)
        with self.assertRaises(IdentityError):
            verify_session_token(bootstrap, self.private_key.public_key(), self.users, expected_context=self.context)
        with self.assertRaises(IdentityError):
            verify_bootstrap(
                session,
                self.private_key.public_key(),
                self.users,
                expected_context=self.context,
                now=self.now,
            )

    def test_context_mismatch_is_rejected(self):
        token = issue_session_token(self.context, self.private_key, now=self.now)
        for mismatch in (
            replace(self.context, session_id="other-session"),
            replace(self.context, telegram_user_id="67890", role="docente", codigo=None),
            replace(self.context, jti="other-jti"),
        ):
            with self.subTest(mismatch=mismatch):
                with self.assertRaises(IdentityError):
                    verify_session_token(
                        token,
                        self.private_key.public_key(),
                        self.users,
                        expected_context=mismatch,
                    )

    def test_unknown_user_is_rejected(self):
        claims = self._claims("bootstrap", BOOTSTRAP_AUDIENCE)
        claims["sub"] = "99999"
        token = jwt.encode(claims, self.private_key, algorithm="EdDSA")
        with self.assertRaises(IdentityError):
            verify_bootstrap(token, self.private_key.public_key(), self.users, now=self.now)

    def test_role_and_code_claims_are_not_authoritative(self):
        claims = self._claims("session", "mcp-tool-call")
        claims.update({"role": "docente", "codigo": "FAKE-CODE"})
        token = jwt.encode(claims, self.private_key, algorithm="EdDSA")
        verified = verify_session_token(
            token,
            self.private_key.public_key(),
            self.users,
            expected_context=self.context,
            now=self.now,
        )
        self.assertEqual((verified.role, verified.codigo), ("estudiante", "S-1"))

    def _claims(self, token_type: str, audience: str) -> dict[str, object]:
        return {
            "iss": DEFAULT_ISSUER,
            "aud": audience,
            "typ": token_type,
            "jti": self.context.jti,
            "sid": self.context.session_id,
            "sub": self.context.telegram_user_id,
            "iat": self.now,
            "exp": self.now + timedelta(minutes=5),
        }


if __name__ == "__main__":
    unittest.main()
