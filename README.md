# OpenBot: Telegram bot connected to opencode

A Telegram bot that bridges your messages to [opencode](https://opencode.ai). Every message you send is passed to `opencode run` and the reply is returned to the chat, letting you use opencode (and your PC) from your phone.

## Requirements

- Python 3.10+
- opencode installed on your system
- A Telegram bot created with [@BotFather](https://t.me/BotFather)

## Installation

```powershell
git clone <repo-url>
cd opencode-telegram
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Create a `.env` file (not tracked by git) with:

```
TELEGRAM_TOKEN=your_token_from_botfather
```

`OWNER_CHAT_ID` is saved automatically the first time you run `/start`.

### Permissions (`opencode.json`)

Every message spawns `opencode run` in non-interactive mode, so permission requests are auto-rejected. The project config defines what the bot may do without asking:

- `external_directory`: read access to `E:\` and its contents (anything else asks for permission).
- `bash`: only allowed for query commands (`Get-ChildItem`, `ls`, `dir`, `Test-Path`, `where`).

Adjust the paths and commands to match what you want to allow.

## Usage

```powershell
python -u bot.py
```

In Telegram:

- `/start` — verifies the connection and registers your chat as the only authorized one
- `/ayuda` — shows the available commands
- `/reset` — full bot restart: clears the owner and all opencode sessions, then relaunches the process
- Any other message — sent to opencode and the reply is returned

Logs are written to `bot.log` and `bot.err.log`.

## How it works

1. The bot listens for messages from the authorized chat (only that `chat_id` gets replies).
2. Each chat keeps its own opencode session in `sessions.json`; the first message creates a session and subsequent messages continue it via `opencode run --format json --session <id> --title telegram-bot <prompt>`, so opencode remembers the conversation per chat.
3. The JSON output is parsed to extract the reply text and the session id.
4. The output is cleaned and split into chunks of 4096 characters to respect Telegram's message limit.

> Use `/reset` to wipe the conversation memory (and the owner registration) and start fresh. `sessions.json` is git-ignored.

## Troubleshooting

- **`[WinError 2]` when sending a message**: on Windows, npm installs opencode as a `.cmd` shim that cannot be executed directly. The bot resolves it automatically by looking for the real exe at `%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe`, but you must restart the bot after updating.
- **Rejected permissions (`permission requested... auto-rejecting`)**: the process is non-interactive; add the matching rule to `opencode.json` (see the Permissions section).