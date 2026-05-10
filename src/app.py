import os
import threading
import secrets
from flask import Flask
from dotenv import load_dotenv

from src.extensions import socketio
from src.state import load_stats_cache
from src.socket_events import register_socket_events
from src.background_tasks import stats_poller, sysinfo_poller, models_poller
from src.routes.views import views_bp
from src.routes.auth import auth_bp
from src.routes.api import api_bp


def create_app(ui_password=None):
    load_dotenv()

    app = Flask(__name__)

    using_generated_password = False

    if not ui_password:
        ui_password = os.getenv("UI_PASSWORD")

    if not ui_password:
        ui_password = secrets.token_hex(8)
        using_generated_password = True
        print("\n" + "=" * 60)
        print(
            f"⚠️  NO UI_PASSWORD SET. YOUR AUTO-GENERATED PASSWORD IS: {ui_password}")
        print("This password has been saved to your .env file for future logins.")
        print("=" * 60 + "\n")

        # Attempt to save it to .env
        try:
            with open(".env", "a") as f:
                f.write(f"\nUI_PASSWORD={ui_password}\n")
        except Exception:
            pass

    # Handle persistent SECRET_KEY
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_hex(32)
        # Attempt to save it to .env
        try:
            with open(".env", "a") as f:
                f.write(f"\nSECRET_KEY={secret_key}\n")
        except Exception:
            pass

    app.config['SECRET_KEY'] = secret_key
    app.config['UI_PASSWORD'] = ui_password
    app.config['USING_GENERATED_PASSWORD'] = using_generated_password
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    if os.getenv("FLASK_ENV") == "production" or os.getenv("USE_SECURE_COOKIES") == "true":
        app.config['SESSION_COOKIE_SECURE'] = True

    app.register_blueprint(views_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    socketio.init_app(app, cors_allowed_origins="*")
    register_socket_events()

    load_stats_cache()

    return app


def start_background_tasks():
    threading.Thread(target=stats_poller, daemon=True).start()
    threading.Thread(target=sysinfo_poller, daemon=True).start()
    threading.Thread(target=models_poller, daemon=True).start()
