"""Emisión y validación de identidad firmada para procesos MCP.

La clave privada solo se usa en el proceso principal. El servidor MCP recibe
únicamente la clave pública y resuelve rol y código mediante ``UserRepository``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from access import Role
from sessions import SessionError, UserRecord, UserRepository

BOOTSTRAP_AUDIENCE: Final = "mcp-bootstrap"
SESSION_AUDIENCE: Final = "mcp-tool-call"
BOOTSTRAP_TYPE: Final = "bootstrap"
SESSION_TYPE: Final = "session"
ALGORITHM: Final = "EdDSA"
DEFAULT_ISSUER: Final = "upb-r6"
DEFAULT_BOOTSTRAP_LIFETIME_SECONDS: Final = 30
DEFAULT_SESSION_LIFETIME_SECONDS: Final = 600
_REQUIRED_CLAIMS: Final[tuple[str, ...]] = (
    "iss",
    "aud",
    "typ",
    "jti",
    "sid",
    "sub",
    "iat",
    "exp",
)
_IDENTIFIER_CLAIM_MAX_LENGTH: Final = 256


class IdentityError(ValueError):
    """Error controlado al emitir, cargar o validar identidad MCP."""


@dataclass(frozen=True)
class ProcessIdentityContext:
    """Identidad inmutable esperada por un proceso MCP dedicado."""

    session_id: str
    telegram_user_id: str
    jti: str
    role: Role
    codigo: str | None


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Carga una clave privada Ed25519 desde PEM sin exponerla en errores."""
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise IdentityError("No se pudo cargar la clave privada Ed25519.") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise IdentityError("La clave privada configurada no es Ed25519.")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Carga una clave pública Ed25519 desde PEM."""
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise IdentityError("No se pudo cargar la clave pública Ed25519.") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise IdentityError("La clave pública configurada no es Ed25519.")
    return key


def context_from_user(
    *,
    session_id: str,
    telegram_user_id: str,
    jti: str,
    users: UserRepository,
) -> ProcessIdentityContext:
    """Construye el contexto desde el repositorio confiable de usuarios."""
    normalized_session_id = _validate_identifier(session_id, "session_id")
    normalized_jti = _validate_identifier(jti, "jti")
    try:
        user = users.resolve(telegram_user_id)
    except (SessionError, ValueError) as exc:
        raise IdentityError("No se pudo resolver el usuario del contexto MCP.") from exc
    return _context_from_user(
        session_id=normalized_session_id,
        telegram_user_id=user.telegram_user_id,
        jti=normalized_jti,
        user=user,
    )


def issue_bootstrap(
    context: ProcessIdentityContext,
    private_key: Ed25519PrivateKey,
    *,
    issuer: str = DEFAULT_ISSUER,
    now: datetime | None = None,
    lifetime_seconds: int = DEFAULT_BOOTSTRAP_LIFETIME_SECONDS,
) -> str:
    """Emite un bootstrap firmado para inicializar un proceso MCP."""
    return _issue_token(
        context,
        private_key,
        issuer=issuer,
        audience=BOOTSTRAP_AUDIENCE,
        token_type=BOOTSTRAP_TYPE,
        now=now,
        lifetime_seconds=lifetime_seconds,
    )


def issue_session_token(
    context: ProcessIdentityContext,
    private_key: Ed25519PrivateKey,
    *,
    issuer: str = DEFAULT_ISSUER,
    now: datetime | None = None,
    lifetime_seconds: int = DEFAULT_SESSION_LIFETIME_SECONDS,
) -> str:
    """Emite el JWT enviado en ``_meta`` durante llamadas MCP."""
    return _issue_token(
        context,
        private_key,
        issuer=issuer,
        audience=SESSION_AUDIENCE,
        token_type=SESSION_TYPE,
        now=now,
        lifetime_seconds=lifetime_seconds,
    )


def verify_bootstrap(
    token: str,
    public_key: Ed25519PublicKey,
    users: UserRepository,
    *,
    issuer: str = DEFAULT_ISSUER,
    expected_context: ProcessIdentityContext | None = None,
    now: datetime | None = None,
) -> ProcessIdentityContext:
    """Verifica un bootstrap y resuelve su contexto desde ``usuarios.json``."""
    return _verify_token(
        token,
        public_key,
        users,
        issuer=issuer,
        audience=BOOTSTRAP_AUDIENCE,
        token_type=BOOTSTRAP_TYPE,
        expected_context=expected_context,
        now=now,
    )


def verify_session_token(
    token: str,
    public_key: Ed25519PublicKey,
    users: UserRepository,
    *,
    issuer: str = DEFAULT_ISSUER,
    expected_context: ProcessIdentityContext,
    now: datetime | None = None,
) -> ProcessIdentityContext:
    """Verifica un JWT de llamada y exige coincidencia con el proceso."""
    return _verify_token(
        token,
        public_key,
        users,
        issuer=issuer,
        audience=SESSION_AUDIENCE,
        token_type=SESSION_TYPE,
        expected_context=expected_context,
        now=now,
    )


def _issue_token(
    context: ProcessIdentityContext,
    private_key: Ed25519PrivateKey,
    *,
    issuer: str,
    audience: str,
    token_type: str,
    now: datetime | None,
    lifetime_seconds: int,
) -> str:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise IdentityError("La clave de emisión no es Ed25519.")
    if not issuer.strip() or lifetime_seconds <= 0:
        raise IdentityError("La configuración del token no es válida.")
    _validate_context_identifiers(context)
    current = _as_utc(now or datetime.now(timezone.utc))
    claims = {
        "iss": issuer,
        "aud": audience,
        "typ": token_type,
        "jti": context.jti,
        "sid": context.session_id,
        "sub": context.telegram_user_id,
        "iat": current,
        "exp": current + timedelta(seconds=lifetime_seconds),
    }
    try:
        return jwt.encode(claims, private_key, algorithm=ALGORITHM)
    except (TypeError, ValueError) as exc:
        raise IdentityError("No se pudo emitir el token MCP.") from exc


def _verify_token(
    token: str,
    public_key: Ed25519PublicKey,
    users: UserRepository,
    *,
    issuer: str,
    audience: str,
    token_type: str,
    expected_context: ProcessIdentityContext | None,
    now: datetime | None,
) -> ProcessIdentityContext:
    if not isinstance(token, str) or not token.strip():
        raise IdentityError("Token MCP ausente o inválido.")
    if not isinstance(public_key, Ed25519PublicKey):
        raise IdentityError("La clave de verificación no es Ed25519.")
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={
                "require": list(_REQUIRED_CLAIMS),
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except (jwt.InvalidTokenError, TypeError, ValueError) as exc:
        raise IdentityError("Token MCP inválido.") from exc

    if claims.get("typ") != token_type:
        raise IdentityError("Propósito de token MCP inválido.")
    _validate_temporal_claims(claims, now)
    try:
        session_id = _validate_identifier(claims["sid"], "session_id")
        telegram_user_id = _validate_identifier(claims["sub"], "telegram_user_id")
        jti = _validate_identifier(claims["jti"], "jti")
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityError("Identidad del token MCP inválida.") from exc

    try:
        user = users.resolve(telegram_user_id)
    except (SessionError, ValueError) as exc:
        raise IdentityError("Usuario del token MCP inexistente.") from exc
    context = _context_from_user(
        session_id=session_id,
        telegram_user_id=telegram_user_id,
        jti=jti,
        user=user,
    )
    if expected_context is not None and context != expected_context:
        raise IdentityError("El token MCP no coincide con el contexto esperado.")
    return context


def _context_from_user(
    *,
    session_id: str,
    telegram_user_id: str,
    jti: str,
    user: UserRecord,
) -> ProcessIdentityContext:
    if user.telegram_user_id != telegram_user_id:
        raise IdentityError("El usuario no coincide con el telegram_user_id.")
    return ProcessIdentityContext(
        session_id=session_id,
        telegram_user_id=telegram_user_id,
        jti=jti,
        role=user.role,
        codigo=user.student_code,
    )


def _validate_context_identifiers(context: ProcessIdentityContext) -> None:
    _validate_identifier(context.session_id, "session_id")
    _validate_identifier(context.telegram_user_id, "telegram_user_id")
    _validate_identifier(context.jti, "jti")


def _validate_identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise IdentityError(f"{name} inválido.")
    normalized = value.strip()
    if not normalized or len(normalized) > _IDENTIFIER_CLAIM_MAX_LENGTH:
        raise IdentityError(f"{name} inválido.")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise IdentityError("La fecha del token debe incluir zona horaria.")
    return value.astimezone(timezone.utc)


def _validate_temporal_claims(claims: dict[str, object], now: datetime | None) -> None:
    current = _as_utc(now or datetime.now(timezone.utc)).timestamp()
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or issued_at > current
        or expires_at <= current
        or expires_at <= issued_at
    ):
        raise IdentityError("Vigencia del token MCP inválida.")
