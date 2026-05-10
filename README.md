# HordeDash

A web-based management dashboard for [AI Horde](https://aihorde.net/) workers (reGen). Monitor stats, manage processes, and edit configuration from a single interface.

---

## Features

- **Process Management**: Start, stop, and graceful drain management for the worker process.
- **Real-time Stats**: 
  - GPU utilization and memory usage (via `nvidia-smi`).
  - Session performance: Kudos, jobs completed, and kudos/hr.
  - Account overview: Total kudos, worker count, and fulfillment stats.
- **Live Logs**: Stream worker output directly to the browser via WebSockets.
- **Config Editor**: Unified interface for `bridgeData.yaml` and Dashboard settings.
- **Remote Access**: Built-in ngrok integration with automatic port synchronization.
- **Maintenance**: Trigger model downloads and git updates.
- **Security**: Password protection, sensitive key masking, and session invalidation on credential change.

---

## Installation

### Windows
1. Clone the repo:
   ```bash
   git clone https://github.com/Devqo/HordeDash.git
   cd HordeDash
   ```
2. Run the setup script:
   ```cmd
   setup.cmd --password [OPTIONAL_PWD] --port [OPTIONAL_PORT]
   ```
   *Flags used during setup are saved permanently to your `.env` file.*

### Linux / Mac
- **Manual**: `chmod +x setup.sh && ./setup.sh --password [PWD] --port [PORT]`

### Docker
- **Docker Compose**: `docker-compose up -d`

---

## Usage

### Launching
Start the UI with:
```cmd
start_ui.cmd --password [PASSWORD] (optional) --port [PORT] (default: 7860)
```
*Flags used with `start_ui` are session-only and will not overwrite your permanent `.env` settings.*

### Remote Access
Configure your **ngrok Auth Token** directly in the Settings tab. The dashboard will automatically handle the tunnel and port mapping. Dashboard-specific settings are now stored securely in `.env` to keep your worker logs clean.

---

## License

See `LICENSE`.
