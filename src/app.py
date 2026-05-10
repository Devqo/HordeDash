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
from src.utils.config import update_env_file


def create_app(ui_password=None, port=None, persist_config=False):
    load_dotenv()

    app = Flask(__name__)

    # Precedence: CLI > .env > Default
    final_port = port or int(os.getenv("PORT", 7860))
    app.config['PORT'] = final_port

    if persist_config and port:
        update_env_file("PORT", final_port)
        print(f"[v] Port {final_port} saved permanently to .env")

    using_generated_password = False
    active_password = ui_password or os.getenv("UI_PASSWORD")

    if not active_password:
        active_password = secrets.token_hex(8)
        using_generated_password = True
        print("\n" + "=" * 60)
        print(f"⚠️  NO UI_PASSWORD SET. GENERATED: {active_password}")
        print("This password has been saved to your .env file.")
        print("=" * 60 + "\n")
        update_env_file("UI_PASSWORD", active_password)
    elif persist_config and ui_password:
        update_env_file("UI_PASSWORD", active_password)
        print("[v] Password saved permanently to .env")

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_hex(32)
        update_env_file("SECRET_KEY", secret_key)

    # Bind session integrity to the current password. Changing the password
    # rotates the signing key, effectively invalidating all active sessions.
    app.config['SECRET_KEY'] = f"{secret_key}_{active_password}"
    app.config['UI_PASSWORD'] = active_password
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
