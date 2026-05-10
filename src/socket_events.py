from flask import request, session

from src.extensions import socketio

def register_socket_events():
    @socketio.on('connect')
    def handle_connect():
        if not session.get('authenticated'):
            return False
