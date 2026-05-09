# HordeDash

A web-based management dashboard for [AI Horde](https://aihorde.net/) workers (reGen). Monitor stats, manage processes, and edit configuration from a single interface.

---

## Features

- **Process Management**: Start, stop, and force-kill the worker process.
- **Real-time Stats**: 
  - GPU utilization and memory usage (via `nvidia-smi`).
  - Session performance: Kudos, jobs completed, and kudos/hr.
  - Account overview: Total kudos, worker count, and fulfillment stats.
- **Live Logs**: Stream worker output directly to the browser via WebSockets.
- **Config Editor**: Edit `bridgeData.yaml` in-browser.
- **Remote Access**: Built-in ngrok integration.
- **Maintenance**: Trigger model downloads and git updates.
- **Security**: Password protection and sensitive key masking for remote sessions.

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
   setup.cmd
   ```
   *This clones the worker repo, creates a venv, and installs dependencies.*

### Linux / Docker
- **Manual**: `chmod +x setup.sh && ./setup.sh`
- **Docker**: `docker-compose up -d`

---

## Usage

### Launching
Start the UI with:
```cmd
start_ui.cmd --password [PASSWORD] (optional) --port [PORT] (default: 7860)
```
If no password is provided, a random token will be generated on startup.

### Remote Access
Configure your **ngrok Auth Token** in the Settings tab or add `ngrok_authtoken: "YourToken"` to bridgeData.yaml to expose the dashboard to the web.

---

## License

See `LICENSE`.