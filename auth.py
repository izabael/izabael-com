"""Authentication helpers for izabael.com.

Cookie-based sessions via Starlette SessionMiddleware.
No extra dependencies — uses stdlib hashlib + secrets.
"""

from fastapi import Request
from database import get_user_by_id


async def get_current_user(request: Request) -> dict | None:
    """Extract the logged-in user from the session cookie.

    Returns user dict or None. Never raises — pages that require
    auth should check the return value themselves.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return await get_user_by_id(user_id)


def login_session(request: Request, user: dict) -> None:
    """Set session data after successful login."""
    request.session["user_id"] = user["id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]


def logout_session(request: Request) -> None:
    """Clear session data."""
    request.session.clear()


def is_admin(user: dict | None) -> bool:
    """Check if user has admin role."""
    return user is not None and user.get("role") == "admin"
