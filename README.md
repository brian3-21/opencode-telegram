# OpenBot: Telegram bot connected to opencode

A Telegram bot that bridges your messages to [opencode](https://opencode.ai). Every message you send is passed to `opencode run` and the reply is returned to the chat, letting you use opencode (and your PC) from your phone.

## Requirements

- Python 3.10+
- opencode installed on your system
- A Telegram bot created with [@BotFather](https://t.me/BotFather)

## Installation

### Windows (PowerShell)

```powershell
git clone <repo-url>
cd opencode-telegram
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### NixOS (flakes) — Run

```bash
nix run github:brian3-21/opencode-telegram
```

`opencode` and `git` are the only dependencies not handled by Nix — install them separately and keep them on your `PATH`. `git` is optional: without it, `/state` and `.bot.pid` just report `unknown` for the commit. The built binary reads `.env` from and writes all runtime state (`sessions.json`, `bot.log`, `.bot.pid`, `.bot.lock`) to `~/.config/opencode-telegram/`.

#### NixOS (flakes) — Development

```bash
git clone https://github.com/brian3-21/opencode-telegram
cd opencode-telegram
nix develop          # dev shell with python + deps + git
python bot.py        # run from the source tree; state lives in this folder
```

## Configuration

Create a `.env` file (not tracked by git) with:

```
TELEGRAM_TOKEN=your_token_from_botfather
```

`OWNER_CHAT_ID` is saved automatically the first time you run `/start`.

### Routes (`config.json`)

Each section runs `opencode run` inside a named **route** defined in `config.json` (copy `config.example.json`):

```json
{
  "routes": {
    "bot": ".",
    "proyectoA": "E:/codigo/proyectoA",
    "proyectoB": "E:/codigo/proyectoB"
  },
  "default_route": "proyectoA"
}
```

- `routes` maps short names to folders. New sections start on `default_route` unless you pass one explicitly (`/new api proyectoA`). A section with no route falls back to the bot's folder.
- Relative paths (like `"."` or `".."`) resolve against the bot's folder; `~` and environment variables (`%VAR%` / `$VAR`) are expanded. The folder must exist on disk.
- The file is re-read on every message, so changes apply immediately — no restart needed.
- `config.json` is git-ignored because paths are machine-specific; `config.example.json` is tracked as a template. Each PC defines the same route **names** pointing at its own paths, and `sessions.json` only stores the route name, so sections stay meaningful across machines.
- The bot always passes its own `opencode.json` to opencode via `OPENCODE_CONFIG`, so the permission rules keep applying regardless of the working directory.

Note: opencode sessions are tied to the project directory where they were created. If a route is remapped to a different folder, sections using it refuse to run and reply with a clear error — fix `config.json` or recreate the section (`/delete <name>` + `/new <name> <route>`).

### Permissions (`opencode.json`)

Every message spawns `opencode run` in non-interactive mode, so permission requests are auto-rejected. The config defines what the bot may do without asking:

- `external_directory`: read access to `E:\` and its contents (anything else asks for permission).
- `bash`: only allowed for query commands (`Get-ChildItem`, `ls`, `dir`, `Test-Path`, `where`).

Adjust the paths and commands to match what you want to allow.

## Usage

```powershell
python -u bot.py
```

In Telegram:

   - `/start` — verifies the connection and registers your chat as the only authorized one
   - `/help` — shows the available commands
   - `/state` — full status: PID, the last git commit the running instance was launched from, default route, start time and uptime in seconds
   - `/stop` — stops the bot
   - `/sections` — lists your sections (contexts) with their route and shows which one is active
   - `/new <name> [route]` — creates a new section (single-word name) on an optional named route from `config.json` (defaults to `default_route`) and makes it active; omit both to auto-generate a name
   - `/use <name>` — switches to an existing section and tells you its route
   - `/delete <name>` — deletes a section
   - Any other message — sent to opencode within the currently active section

Logs are written to `bot.log` and `bot.err.log`.

## Checking that the bot is active

Because `run_polling` prints nothing while idle and the process is often launched detached, use these signals to confirm the bot is running:

- **`.bot.pid`** — a file written at startup containing the bot's process ID (first line), plus the git commit it was launched from (`commit=...`) and the start time (`started=...`). Verify the process is alive with `Get-Process -Id (Get-Content .bot.pid)` (PowerShell) or `ps -p $(cat .bot.pid)` (Linux/macOS). It is removed automatically on normal exit (it is git-ignored).
- **`/state`** — sends the full status: PID, the last commit the running instance came from, default route, start time and uptime in seconds. Use this to detect a **stale instance** (see below).

### What is a PID and how the status logic works

A **PID (Process ID)** is a number the operating system assigns to every running process so it can identify and manage it (send signals, inspect, or terminate it). At any moment, two running processes never share the same PID.

`OpenBot` writes **its own PID** into `.bot.pid` at startup — and only after it has won the single-instance lock, so the number always belongs to the real running bot. When the bot exits normally, it deletes the file.

To know if the bot is truly alive you do **not** trust the file's presence alone: you read the PID and ask the OS whether a process with that number exists:

- Windows (PowerShell): `Get-Process -Id (Get-Content .bot.pid)`
- Linux/macOS: `ps -p $(cat .bot.pid)`

If the process exists → the bot is running. If it does not → the file is stale (e.g. the bot crashed without cleaning up) and can be safely deleted. This is exactly what `bot_status.ps1` does for you automatically.

> The PID file is just a "note with the number"; the mutex (or file lock) is what actually enforces the single instance. That is why the PID is written *after* acquiring the lock.
- **Heartbeat** — every 5 minutes a background thread logs a line `Bot vivo - HH:MM` to both the console and `bot.log`, so an idle console does not look frozen.
- **`bot_status.ps1`** (Windows) — run `.\bot_status.ps1` to check automatically: it reads `.bot.pid`, confirms the PID exists, and compares the recorded commit against `git rev-parse --short HEAD`. It reports `VIVO y ACTUALIZADO`, `VIVO pero DESACTUALIZADO` (the running instance is older than the code on disk — restart the bot), or `DETENIDO`.
- **Logs** — `bot.log` records every message, opencode run, and the periodic heartbeat.
- **Single-instance mutex test** — only one instance may run at a time (a named mutex `Global\OpenBotTelegramInstance` on Windows, or a file lock on `.bot.lock` elsewhere). Launching a second copy immediately exits with `Ya hay una instancia del bot corriendo`. This proves the running process is the unique one.

> **Catching a stale instance:** because `.bot.pid` records the commit the bot launched from, you can tell if a long-running instance is behind the code on disk (e.g. you edited `bot.py` or pulled new commits but never restarted). Compare the `commit=` line with `git rev-parse --short HEAD`, or just run `bot_status.ps1`. If they differ, stop the bot (with `/stop` or Ctrl+C) and relaunch `python -u bot.py` so the new code takes effect.

## How it works

1. The bot listens for messages from the authorized chat (only that `chat_id` gets replies).
2. Each chat keeps its own opencode sessions organized into **sections** in `sessions.json`. A section is an isolated opencode conversation bound to a named **route**: the first message in a section creates its session and subsequent messages continue it via `opencode run --format json --session <id> --title <section> <prompt>`, so opencode remembers the conversation per section. Use `/sections`, `/new`, `/use` and `/delete` to manage them. The default section is always `default`; switching sections changes which conversation your next messages feed into.
3. Every `opencode run` subprocess starts in the directory of the active section's route, resolved through `config.json` (the bot's project folder when the section has no route; see Configuration).
4. The JSON output is parsed to extract the reply text and the session id.
5. The output is cleaned and split into chunks of 4096 characters to respect Telegram's message limit.

> Old formats are migrated automatically on first read: a bare `chat_id -> session_id` string becomes the `default` section, and a section stored as a bare session-id string becomes an object `{ "session", "route", "dir" }` (the directory is recorded on first use, and `route` stays empty so the section keeps working in the bot's folder until you recreate it with a route). To wipe all conversation memory, stop the bot (`/stop` or Ctrl+C) and delete `sessions.json` manually; to require re-registration, also remove `OWNER_CHAT_ID` from `.env`. `sessions.json` is git-ignored.

## Troubleshooting

- **`Sin respuesta: opencode termino sin generar texto...`**: the model provider sometimes returns empty completions and opencode exits silently (`rc=0`, no events). The bot automatically retries once; if it keeps happening after the retry, the provider/model is flaky or misconfigured (check your opencode account). The `bot.log` records the raw stdout of failed runs (`opencode devolvio una respuesta vacia`).
- **`[WinError 2]` when sending a message**: on Windows, npm installs opencode as a `.cmd` shim that cannot be executed directly. The bot resolves it automatically by looking for the real exe at `%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe`, but you must restart the bot after updating.
- **Rejected permissions (`permission requested... auto-rejecting`)**: the process is non-interactive; add the matching rule to `opencode.json` (see the Permissions section).
- **`Ya hay una instancia del bot corriendo` on Linux/macOS** (the `.bot.lock` trap): on non-Windows systems the single-instance lock is a file lock on `.bot.lock` instead of a system mutex. If the bot crashes hard (kill -9, power loss) the lock may not be released and the file can be left behind, so the next launch wrongly thinks another instance is running and exits. Fix: delete `.bot.lock` manually and relaunch. On Windows this does not happen, because the mutex is released automatically by the OS when the process ends. (This note is for future use if you run the bot on a Linux/macOS machine; on Windows it is irrelevant.)
