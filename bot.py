import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SESSIONS_FILE = BASE_DIR / "sessions.json"
MAX_MESSAGE_LEN = 4096

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
WHITESPACE_RE = re.compile(r"[ \t]+")


def resolve_env() -> dict:
    return dotenv_values(ENV_FILE)


def get_owner_chat_id() -> str:
    return resolve_env().get("OWNER_CHAT_ID", "").strip()


def save_owner_chat_id(chat_id: int) -> None:
    set_key(str(ENV_FILE), "OWNER_CHAT_ID", str(chat_id))


def is_owner(chat_id: int) -> bool:
    owner = get_owner_chat_id()
    return bool(owner) and str(chat_id) == owner


def auth_error(chat_id: int) -> str:
    owner = get_owner_chat_id()
    if not owner:
        return "No hay dueno registrado. Envía /start para registrar este chat como dueno."
    return (
        "No autorizado. (Tu chat_id es "
        + str(chat_id)
        + "; el dueno registrado es "
        + owner
        + ".)"
    )


def load_sessions() -> dict:
    if SESSIONS_FILE.is_file():
        try:
            return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_sessions(data: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_session_id(chat_id: int) -> str:
    return load_sessions().get(str(chat_id), "")


def set_session_id(chat_id: int, session_id: str) -> None:
    data = load_sessions()
    data[str(chat_id)] = session_id
    save_sessions(data)


def clear_sessions() -> None:
    if SESSIONS_FILE.is_file():
        SESSIONS_FILE.unlink()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not get_owner_chat_id():
        save_owner_chat_id(chat_id)
        owner = get_owner_chat_id()
        await update.message.reply_text(
            "Hola, soy tu bot conectado a opencode.\n"
            "Te he registrado como el unico autorizado (chan_id " + str(owner) + ").\n"
            "Envia cualquier mensaje para consultar a opencode."
        )
        return
    if is_owner(chat_id):
        await update.message.reply_text(
            "Hola de nuevo. Envia cualquier mensaje para consultar a opencode,\no /ayuda para ver los comandos."
        )
    else:
        await update.message.reply_text(auth_error(chat_id))


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    await update.message.reply_text(
        "/start - Verificar conexion\n"
        "/ayuda - Mostrar esta ayuda\n"
        "/reset - Reinicio total del bot (borra dueno y sesiones)\n"
        "Cualquier otro mensaje se envia a opencode (opencode run)."
    )


def clean_output(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def find_opencode_exe() -> str:
    if os.name != "nt":
        return shutil.which("opencode") or "opencode"
    exe = shutil.which("opencode.exe")
    if exe:
        return exe
    shim = shutil.which("opencode")
    if shim:
        npm_dir = Path(shim).resolve().parent / "node_modules"
        for pkg in ("opencode-ai", "@opencode-ai/opencode"):
            cand = npm_dir / pkg / "bin" / "opencode.exe"
            if cand.is_file():
                return str(cand)
        return shim
    return "opencode"


OPENCODE_EXE = find_opencode_exe()


def parse_opencode_output(text: str) -> tuple[str, str]:
    session_id = ""
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = event.get("sessionID")
        if sid and not session_id:
            session_id = sid
        if event.get("type") == "text":
            part = event.get("part") or {}
            t = part.get("text")
            if t:
                chunks.append(t)
    return session_id, "\n".join(chunks).strip()


async def run_opencode(prompt: str, session_id: str = "", timeout: int = 600) -> tuple[str, str]:
    args = [OPENCODE_EXE, "run", "--format", "json", "--title", "telegram-bot"]
    if session_id:
        args += ["--session", session_id]
    args.append(prompt)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    session_id_out, reply = parse_opencode_output(out)
    if not reply.strip():
        reply = clean_output(err) or "Sin respuesta."
    return reply, session_id_out


def split_long(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_owner(chat_id):
        await update.message.reply_text(auth_error(chat_id))
        return

    prompt = update.message.text
    if not prompt:
        return

    waiting = await update.message.reply_text("Pensando...")
    sid = get_session_id(chat_id)
    try:
        reply, new_sid = await run_opencode(prompt, sid)
        if new_sid:
            set_session_id(chat_id, new_sid)
    except asyncio.TimeoutError:
        reply = "La consulta tardo demasiado y fue cancelada."
    except Exception as exc:
        reply = "Error ejecutando opencode: " + str(exc)

    await waiting.delete()
    for chunk in split_long(reply):
        await update.message.reply_text(chunk)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    save_owner_chat_id("")
    clear_sessions()
    await update.message.reply_text("Configuracion borrada. Reiniciando el bot...")
    await context.application.stop()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:])


def main() -> None:
    token = resolve_env().get("TELEGRAM_TOKEN", "").strip()
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN en .env")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot iniciado. Esperando mensajes...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()