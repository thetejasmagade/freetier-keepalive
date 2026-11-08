from flask import Flask
from flask_wtf.csrf import CSRFProtect

from .config import settings

csrf = CSRFProtect()


def create_app() -> Flask:
    settings.validate()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.secure_cookies,
    )
    csrf.init_app(app)

    from .routes import bp

    app.register_blueprint(bp)
    return app
