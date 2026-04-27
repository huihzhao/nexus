"""Authentication router and utilities.

Handles JWT and WebAuthn authentication flows:
  - User registration (simple display_name)
  - JWT login
  - WebAuthn passkey registration and login
  - JWT token creation and verification
"""

import json
import logging
import uuid
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from nexus_server.config import get_config
from nexus_server.database import get_db_connection

logger = logging.getLogger(__name__)
config = get_config()

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ───────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ───────────────────────────────────────────────────────────────────────────


class UserRegisterRequest(BaseModel):
    """User registration request."""

    display_name: str = Field(..., min_length=1, max_length=255)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        """Validate display name."""
        if not v.strip():
            raise ValueError("Display name cannot be empty")
        return v.strip()


class UserRegisterResponse(BaseModel):
    """User registration response."""

    user_id: str
    jwt_token: str
    created_at: str


class UserLoginRequest(BaseModel):
    """User login request."""

    user_id: str = Field(..., min_length=1)


class UserLoginResponse(BaseModel):
    """User login response."""

    jwt_token: str
    expires_in_seconds: int


class WebAuthnRegisterStartRequest(BaseModel):
    """WebAuthn registration start request."""

    display_name: str = Field(..., min_length=1, max_length=255)
    user_agent: Optional[str] = None


class WebAuthnRegisterStartResponse(BaseModel):
    """WebAuthn registration start response."""

    challenge: str
    user_id: str
    rp_id: str
    rp_name: str


class WebAuthnRegisterFinishRequest(BaseModel):
    """WebAuthn registration finish request."""

    user_id: str
    display_name: str
    credential: dict


class WebAuthnRegisterFinishResponse(BaseModel):
    """WebAuthn registration finish response."""

    user_id: str
    jwt_token: str
    credential_id: str


class WebAuthnLoginStartRequest(BaseModel):
    """WebAuthn login start request."""

    user_id: Optional[str] = None


class WebAuthnLoginStartResponse(BaseModel):
    """WebAuthn login start response."""

    challenge: str
    rp_id: str


class WebAuthnLoginFinishRequest(BaseModel):
    """WebAuthn login finish request."""

    user_id: str
    assertion: dict


class WebAuthnLoginFinishResponse(BaseModel):
    """WebAuthn login finish response."""

    jwt_token: str
    expires_in_seconds: int


# ───────────────────────────────────────────────────────────────────────────
# Token Helpers
# ───────────────────────────────────────────────────────────────────────────


def create_jwt_token(user_id: str, jwt_secret: str) -> tuple[str, int]:
    """Create JWT token for user.

    Args:
        user_id: User identifier
        jwt_secret: User-specific secret for token signing

    Returns:
        (token, expires_in_seconds)
    """
    expiration_hours = config.JWT_EXPIRATION_HOURS
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)

    payload = {
        "user_id": user_id,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }

    token = jwt.encode(
        payload, jwt_secret, algorithm=config.JWT_ALGORITHM
    )
    expires_in = int(expiration_hours * 3600)

    return token, expires_in


def verify_jwt_token(token: str, user_id: str) -> bool:
    """Verify JWT token signature and expiry.

    Args:
        token: JWT token to verify
        user_id: Expected user in token

    Returns:
        True if valid, False otherwise
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT jwt_secret FROM users WHERE id = ?",
                           (user_id,))
            row = cursor.fetchone()

        if not row:
            return False

        jwt_secret = row[0]
        payload = jwt.decode(
            token, jwt_secret, algorithms=[config.JWT_ALGORITHM]
        )
        return payload.get("user_id") == user_id

    except jwt.ExpiredSignatureError:
        logger.warning(f"JWT token expired for user {user_id}")
        return False
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token for user {user_id}: {e}")
        return False


async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> str:
    """Dependency to get current authenticated user.

    Args:
        authorization: Authorization header (Bearer <token>)

    Returns:
        Authenticated user ID

    Raises:
        HTTPException: If authorization fails
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
        )

    # Extract user_id from token
    try:
        unverified = jwt.decode(
            token, options={"verify_signature": False}
        )
        user_id = unverified.get("user_id")
    except jwt.DecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if not verify_jwt_token(token, user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return user_id


# ───────────────────────────────────────────────────────────────────────────
# WebAuthn Helpers
# ───────────────────────────────────────────────────────────────────────────


def generate_webauthn_challenge() -> str:
    """Generate a random WebAuthn challenge.

    Returns:
        Base64-encoded challenge
    """
    return b64encode(uuid.uuid4().bytes).decode("utf-8").rstrip("=")


# ───────────────────────────────────────────────────────────────────────────
# Routes
# ───────────────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=UserRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: UserRegisterRequest,
) -> UserRegisterResponse:
    """Register a new user.

    Args:
        request: Registration request with display_name

    Returns:
        user_id and JWT token

    Raises:
        HTTPException: If registration fails
    """
    user_id = str(uuid.uuid4())
    jwt_secret = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users
                (id, display_name, jwt_secret, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, request.display_name, jwt_secret, now, now),
            )
            conn.commit()

        token, _ = create_jwt_token(user_id, jwt_secret)
        logger.info(f"User registered: {user_id}")

        return UserRegisterResponse(
            user_id=user_id,
            jwt_token=token,
            created_at=now,
        )
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed",
        )


@router.post("/login", response_model=UserLoginResponse)
async def login_user(request: UserLoginRequest) -> UserLoginResponse:
    """Login user and return JWT token.

    Args:
        request: Login request with user_id

    Returns:
        JWT token

    Raises:
        HTTPException: If user not found
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT jwt_secret FROM users WHERE id = ?",
                (request.user_id,),
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        jwt_secret = row[0]
        token, expires_in = create_jwt_token(request.user_id, jwt_secret)
        logger.info(f"User logged in: {request.user_id}")

        return UserLoginResponse(
            jwt_token=token,
            expires_in_seconds=expires_in,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Login failed",
        )


@router.post(
    "/passkey/register/start",
    response_model=WebAuthnRegisterStartResponse,
)
async def passkey_register_start(
    request: WebAuthnRegisterStartRequest,
) -> WebAuthnRegisterStartResponse:
    """Start WebAuthn registration.

    Args:
        request: Registration start request

    Returns:
        Challenge and registration options
    """
    user_id = str(uuid.uuid4())
    challenge = generate_webauthn_challenge()

    logger.info(f"WebAuthn registration started for user: {user_id}")

    return WebAuthnRegisterStartResponse(
        challenge=challenge,
        user_id=user_id,
        rp_id=config.WEBAUTHN_RP_ID,
        rp_name=config.WEBAUTHN_RP_NAME,
    )


@router.post(
    "/passkey/register/finish",
    response_model=WebAuthnRegisterFinishResponse,
)
async def passkey_register_finish(
    request: WebAuthnRegisterFinishRequest,
) -> WebAuthnRegisterFinishResponse:
    """Finish WebAuthn registration and create user.

    Args:
        request: Registration finish request with credential

    Returns:
        user_id, JWT token, and credential_id

    Raises:
        HTTPException: If registration fails
    """
    try:
        jwt_secret = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        credential_json = json.dumps(request.credential)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users
                (id, display_name, passkey_credential, jwt_secret,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request.user_id,
                    request.display_name,
                    credential_json,
                    jwt_secret,
                    now,
                    now,
                ),
            )
            conn.commit()

        token, _ = create_jwt_token(request.user_id, jwt_secret)
        credential_id = request.credential.get("id", "unknown")

        logger.info(f"WebAuthn registration finished for user: "
                    f"{request.user_id}")

        return WebAuthnRegisterFinishResponse(
            user_id=request.user_id,
            jwt_token=token,
            credential_id=credential_id,
        )
    except Exception as e:
        logger.error(f"WebAuthn registration finish error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WebAuthn registration failed",
        )


@router.post(
    "/passkey/login/start",
    response_model=WebAuthnLoginStartResponse,
)
async def passkey_login_start(
    request: WebAuthnLoginStartRequest,
) -> WebAuthnLoginStartResponse:
    """Start WebAuthn login.

    Args:
        request: Login start request

    Returns:
        Challenge for assertion
    """
    challenge = generate_webauthn_challenge()
    logger.info("WebAuthn login started")

    return WebAuthnLoginStartResponse(
        challenge=challenge,
        rp_id=config.WEBAUTHN_RP_ID,
    )


@router.post(
    "/passkey/login/finish",
    response_model=WebAuthnLoginFinishResponse,
)
async def passkey_login_finish(
    request: WebAuthnLoginFinishRequest,
) -> WebAuthnLoginFinishResponse:
    """Finish WebAuthn login.

    Args:
        request: Login finish request with assertion

    Returns:
        JWT token

    Raises:
        HTTPException: If verification fails
    """
    try:
        # The frontend currently passes assertion.id (the credential id, a
        # base64url string) in request.user_id. Match users by the credential
        # id stored in passkey_credential.id — NEVER fall back to "most recent
        # user", which would silently hand a fresh login the wrong account.
        credential_id = (request.assertion or {}).get("id") or request.user_id

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 1) Direct match: request.user_id is an actual UUID we issued
            cursor.execute(
                "SELECT id, jwt_secret FROM users WHERE id = ?",
                (request.user_id,),
            )
            row = cursor.fetchone()

            # 2) Match by credential id stored on the user
            if not row and credential_id:
                cursor.execute(
                    "SELECT id, jwt_secret FROM users "
                    "WHERE json_extract(passkey_credential, '$.id') = ?",
                    (credential_id,),
                )
                row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No matching passkey found. Please register first.",
            )

        actual_user_id = row[0]
        jwt_secret = row[1]
        token, expires_in = create_jwt_token(actual_user_id, jwt_secret)

        logger.info(f"WebAuthn login finished for user: {actual_user_id}")

        return WebAuthnLoginFinishResponse(
            jwt_token=token,
            expires_in_seconds=expires_in,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"WebAuthn login finish error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WebAuthn login failed",
        )
