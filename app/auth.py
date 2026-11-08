import hmac
from functools import wraps

from flask import redirect, request, session, url_for

from .config import settings


def password_ok(candidate: str) -> bool:
    return hmac.compare_digest(candidate.encode(), settings.admin_password.encode())


def token_ok() -> bool:
    supplied = request.args.get("token", "")
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        supplied = header[7:]
    return bool(supplied) and hmac.compare_digest(supplied.encode(), settings.run_token.encode())


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)

    return wrapped
