import argparse
from src.app import create_app, start_background_tasks
from src.extensions import socketio
from src.state import PORT

parser = argparse.ArgumentParser(description="HordeUI Control Center")
parser.add_argument(
    "--password", help="Set the UI password (overrides UI_PASSWORD env var)")
parser.add_argument("--port", type=int, default=PORT,
                    help=f"Port to run the UI on (default: {PORT})")
parser.add_argument("--persist-config", action="store_true",
                    help="Permanently save CLI flags to .env (internal use by setup script)")
args, unknown = parser.parse_known_args()

app = create_app(ui_password=args.password,
                 port=args.port, persist_config=args.persist_config)

if __name__ == '__main__':
    start_background_tasks()
    print(f"HordeUI running at http://0.0.0.0:{args.port}")
    socketio.run(app, host='0.0.0.0', port=args.port, debug=False)
