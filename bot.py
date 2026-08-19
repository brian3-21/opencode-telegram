import asyncio
import os
import re
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
        await update.message.reply_text("No autorizado.")


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text("No autorizado.")
        return
    await update.message.reply_text(
        "/start - Verificar conexion\n"
        "/ayuda - Mostrar esta ayuda\n"
        "Cualquier otro mensaje se envia a opencode (opencode run)."
    )


def clean_output(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


async def run_opencode(prompt: str, timeout: int = 600) -> str:
    proc = await asyncio.create_subprocess_exec(
        "opencode",
        "run",
        "--title",
        "telegram-bot",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    out = stdout.decode("utf-8", errors="replace")
    if not out.strip():
        out = stderr.decode("utf-8", errors="replace")
    return clean_output(out) or "Sin respuesta."


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
        await update.message.reply_text("No autorizado.")
        return

    prompt = update.message.text
    if not prompt:
        return

    waiting = await update.message.reply_text("Pensando...")
    try:
        reply = await run_opencode(prompt)
    except asyncio.TimeoutError:
        reply = "La consulta tardo demasiado y fue cancelada."
    except Exception as exc:
        reply = "Error ejecutando opencode: " + str(exc)

    await waiting.delete()
    for chunk in split_long(reply):
        await update.message.reply_text(chunk)


def main() -> None:
    token = resolve_env().get("TELEGRAM_TOKEN", "").strip()
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN en .env")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot iniciado. Esperando mensajes...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()