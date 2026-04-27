"""User profile router.

Handles reading and updating user profile information.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nexus_server.auth import get_current_user
from nexus_server.database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/user", tags=["user"])


# ───────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ───────────────────────────────────────────────────────────────────────────


class UserProfile(BaseModel):
    """User profile."""

    user_id: str
    display_name: str
    created_at: str
    updated_at: str


class UserProfileUpdate(BaseModel):
    """User profile update."""

    display_name: Optional[str] = Field(
        None, min_length=1, max_length=255
    )


# ───────────────────────────────────────────────────────────────────────────
# Routes
# ───────────────────────────────────────────────────────────────────────────


@router.get("/profile", response_model=UserProfile)
async def get_user_profile(
    current_user: str = Depends(get_current_user),
) -> UserProfile:
    """Get current user's profile.

    Args:
        current_user: Authenticated user ID

    Returns:
        User profile

    Raises:
        HTTPException: If user not found
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, display_name, created_at, updated_at
                FROM users WHERE id = ?
                """,
                (current_user,),
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return UserProfile(
            user_id=row[0],
            display_name=row[1],
            created_at=row[2],
            updated_at=row[3],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to retrieve profile",
        )


@router.put("/profile", response_model=UserProfile)
async def update_user_profile(
    request: UserProfileUpdate,
    current_user: str = Depends(get_current_user),
) -> UserProfile:
    """Update user profile.

    Args:
        request: Profile update request
        current_user: Authenticated user ID

    Returns:
        Updated user profile

    Raises:
        HTTPException: If update fails
    """
    try:
        now = datetime.now(timezone.utc).isoformat()

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Get current profile
            cursor.execute(
                """
                SELECT id, display_name, created_at FROM users WHERE id = ?
                """,
                (current_user,),
            )
            row = cursor.fetchone()

            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found",
                )

            display_name = (
                request.display_name if request.display_name else row[1]
            )

            cursor.execute(
                """
                UPDATE users
                SET display_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (display_name, now, current_user),
            )
            conn.commit()

        logger.info(f"User profile updated: {current_user}")

        return UserProfile(
            user_id=row[0],
            display_name=display_name,
            created_at=row[2],
            updated_at=now,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update profile error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update profile",
        )
