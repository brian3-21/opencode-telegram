import asyncio
import ctypes
import json
import atexit
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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
LOCK_FILE = BASE_DIR / ".bot.lock"
PID_FILE = BASE_DIR / ".bot.pid"
MAX_MESSAGE_LEN = 4096
STARTED_AT = 0.0
RUN_COMMIT = "unknown"
_LOCK_HANDLE = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("opencode-telegram")

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
        "/state - Estado del bot: PID, ultimo commit, inicio y tiempo activo (segundos)\n"
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
    log.info("Ejecutando opencode (session=%s): %s", session_id or "<nueva>", prompt[:80])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.error("opencode excedio el timeout de %ss", timeout)
        raise
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    session_id_out, reply = parse_opencode_output(out)
    if not reply.strip():
        reply = clean_output(err) or "Sin respuesta."
    log.info("opencode termino (session=%s, reply_len=%d)", session_id_out or "<nueva>", len(reply))
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

    loop = asyncio.get_event_loop()
    start = loop.time()
    waiting = await update.message.reply_text("Pensando...")
    log.info("Mensaje recibido de %s: %s", chat_id, prompt[:80])

    async def update_status() -> None:
        try:
            while True:
                await asyncio.sleep(15)
                elapsed = int(loop.time() - start)
                try:
                    await waiting.edit_text("Pensando... (" + str(elapsed) + "s)")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    status_task = asyncio.create_task(update_status())
    sid = get_session_id(chat_id)
    reply = "Sin respuesta."
    try:
        reply, new_sid = await run_opencode(prompt, sid)
        if new_sid:
            set_session_id(chat_id, new_sid)
    except asyncio.TimeoutError:
        reply = "La consulta tardo demasiado (>600s) y fue cancelada."
        log.error("Timeout atendiendo mensaje de %s", chat_id)
    except Exception as exc:
        reply = "Error ejecutando opencode: " + str(exc)
        log.exception("Error atendiendo mensaje de %s: %s", chat_id, exc)

    status_task.cancel()
    try:
        await waiting.delete()
    except Exception:
        pass
    for chunk in split_long(reply):
        await update.message.reply_text(chunk)
    log.info("Respuesta enviada a %s (len=%d)", chat_id, len(reply))


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error no capturado en el bot: %s", context.error)
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Error interno del bot: " + str(context.error),
            )
    except Exception:
        pass


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    save_owner_chat_id("")
    clear_sessions()
    await update.message.reply_text("Configuracion borrada. Reiniciando el bot...")
    await context.application.stop()
    remove_pid_file()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:])


def acquire_single_instance() -> None:
    """Allow only one bot process at a time.

    On Windows a named mutex is used (the canonical single-instance mechanism):
    it is system-wide, exclusive across processes, and released automatically
    when the process exits (even on crash). On other systems a file lock is
    used as a fallback. Exits if another instance is already running.
    """
    global _LOCK_HANDLE
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        ERROR_ALREADY_EXISTS = 183
        mutex = kernel32.CreateMutexW(None, 1, "Global\\OpenBotTelegramInstance")
        if not mutex:
            sys.exit("No se pudo crear el mutex de instancia.")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            log.error("Ya hay otra instancia del bot corriendo. Este proceso se detiene.")
            sys.exit("Ya hay una instancia del bot corriendo. Saliendo.")
        _LOCK_HANDLE = mutex
        return

    try:
        _LOCK_HANDLE = open(LOCK_FILE, "a+")
    except OSError as exc:
        sys.exit("No se pudo crear el archivo de bloqueo (.bot.lock): " + str(exc))
    try:
        import fcntl
        fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        log.error("Ya hay otra instancia del bot corriendo. Este proceso se detiene.")
        sys.exit("Ya hay una instancia del bot corriendo. Saliendo.")


def get_repo_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def write_pid_file() -> None:
    try:
        content = (
            str(os.getpid())
            + "\n"
            + "commit="
            + RUN_COMMIT
            + "\n"
            + "started="
            + time.strftime("%Y-%m-%d %H:%M:%S")
            + "\n"
        )
        PID_FILE.write_text(content, encoding="utf-8")
    except OSError as exc:
        log.warning("No se pudo escribir el archivo PID %s: %s", PID_FILE, exc)


def remove_pid_file() -> None:
    try:
        if PID_FILE.is_file():
            PID_FILE.unlink()
    except OSError as exc:
        log.warning("No se pudo borrar el archivo PID %s: %s", PID_FILE, exc)


async def cmd_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    elapsed = int(time.time() - STARTED_AT)
    await update.message.reply_text(
        "📊 Bot state\n"
        "PID: " + str(os.getpid()) + "\n"
        "Last commit: " + RUN_COMMIT + "\n"
        "Started: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(STARTED_AT)) + "\n"
        "Uptime: " + str(elapsed) + "s"
    )


def start_heartbeat() -> None:
    def _loop() -> None:
        while True:
            time.sleep(300)
            log.info("Bot vivo - %s", time.strftime("%H:%M"))

    threading.Thread(target=_loop, daemon=True).start()


def main() -> None:
    global STARTED_AT, RUN_COMMIT
    acquire_single_instance()
    STARTED_AT = time.time()
    RUN_COMMIT = get_repo_commit()
    write_pid_file()
    atexit.register(remove_pid_file)
    token = resolve_env().get("TELEGRAM_TOKEN", "").strip()
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN en .env")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(CommandHandler("state", cmd_state))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    start_heartbeat()
    log.info("Bot iniciado. Esperando mensajes...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()