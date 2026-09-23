import asyncio
import ctypes
import json
import atexit
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import dotenv_values, set_key
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = Path(os.environ.get("OPENBOT_DIR") or Path(__file__).resolve().parent)
ENV_FILE = BASE_DIR / ".env"
SESSIONS_FILE = BASE_DIR / "sessions.json"
LOCK_FILE = BASE_DIR / ".bot.lock"
PID_FILE = BASE_DIR / ".bot.pid"
CONFIG_FILE = BASE_DIR / "config.json"
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


DEFAULT_SECTION = "default"


def load_sessions() -> dict:
    if SESSIONS_FILE.is_file():
        try:
            return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_sessions(data: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clear_sessions() -> None:
    if SESSIONS_FILE.is_file():
        SESSIONS_FILE.unlink()


def _migrate_section(value: object) -> dict:
    """Sections used to be stored as a bare session-id string. The new format
    is an object: session id, route name (portable across machines; each PC's
    config.json resolves it) and the directory the session was created in."""
    if isinstance(value, dict):
        return {
            "session": str(value.get("session") or ""),
            "route": str(value.get("route") or ""),
            "dir": str(value.get("dir") or ""),
        }
    return {"session": str(value or ""), "route": "", "dir": ""}


def _migrate_chat(value: object) -> dict:
    """Old format stored chat_id -> session_id (string). New format stores a
    dict with the current section and a map of section name -> section object."""
    if isinstance(value, dict) and "sections" in value:
        value["sections"] = {
            k: _migrate_section(v) for k, v in (value.get("sections") or {}).items()
        }
        if "current" not in value or value.get("current") not in value["sections"]:
            value["current"] = next(iter(value["sections"]), DEFAULT_SECTION)
        return value
    session_id = value if isinstance(value, str) else ""
    return {"current": DEFAULT_SECTION, "sections": {DEFAULT_SECTION: _migrate_section(session_id)}}


def get_chat_sessions(chat_id: int) -> dict:
    data = load_sessions()
    value = data.get(str(chat_id))
    if value is None:
        return {"current": DEFAULT_SECTION, "sections": {DEFAULT_SECTION: _migrate_section("")}}
    return _migrate_chat(value)


def save_chat_sessions(chat_id: int, chat_data: dict) -> None:
    data = load_sessions()
    data[str(chat_id)] = chat_data
    save_sessions(data)


def get_current_section(chat_id: int) -> str:
    return get_chat_sessions(chat_id).get("current") or DEFAULT_SECTION


def get_section(chat_id: int, name: str) -> dict:
    return get_chat_sessions(chat_id).get("sections", {}).get(name) or _migrate_section("")


def set_section_session(chat_id: int, name: str, session_id: str) -> None:
    chat_data = get_chat_sessions(chat_id)
    section = chat_data["sections"].setdefault(name, _migrate_section(""))
    section["session"] = session_id
    chat_data["current"] = name
    save_chat_sessions(chat_id, chat_data)


def set_section_dir(chat_id: int, name: str, work_dir: str) -> None:
    chat_data = get_chat_sessions(chat_id)
    section = chat_data["sections"].get(name)
    if section is not None and section.get("dir") != work_dir:
        section["dir"] = work_dir
        save_chat_sessions(chat_id, chat_data)


def create_section(chat_id: int, name: str, route: str, work_dir: str) -> bool:
    chat_data = get_chat_sessions(chat_id)
    if name in chat_data["sections"]:
        return False
    chat_data["sections"][name] = {"session": "", "route": route, "dir": work_dir}
    chat_data["current"] = name
    save_chat_sessions(chat_id, chat_data)
    return True


def switch_section(chat_id: int, name: str) -> bool:
    chat_data = get_chat_sessions(chat_id)
    if name in chat_data["sections"]:
        chat_data["current"] = name
        save_chat_sessions(chat_id, chat_data)
        return True
    return False


def delete_section(chat_id: int, name: str) -> bool:
    chat_data = get_chat_sessions(chat_id)
    if name not in chat_data["sections"]:
        return False
    del chat_data["sections"][name]
    if not chat_data["sections"]:
        chat_data["sections"][DEFAULT_SECTION] = _migrate_section("")
    if chat_data["current"] == name:
        chat_data["current"] = next(iter(chat_data["sections"]), DEFAULT_SECTION)
    save_chat_sessions(chat_id, chat_data)
    return True


def list_sections(chat_id: int) -> list[str]:
    return list(get_chat_sessions(chat_id).get("sections", {}).keys())


def route_label(route: str) -> str:
    return route if route else "(carpeta del bot)"


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
            "Hola de nuevo. Envia cualquier mensaje para consultar a opencode,\no /help para ver los comandos."
        )
    else:
        await update.message.reply_text(auth_error(chat_id))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    await update.message.reply_text(
        "/start - Verificar conexion\n"
        "/help - Mostrar esta ayuda\n"
        "/state - Estado del bot: PID, ultimo commit, inicio y tiempo activo (segundos)\n"
        "/stop - Detener el bot\n"
        "/sections - Listar tus secciones (contextos) con su ruta y ver la activa\n"
        "/new <nombre> [ruta] - Crear una seccion nueva y activarla (nombre de una palabra;\n"
        "  ruta opcional definida en config.json; sin args crea una auto con la default_route)\n"
        "/use <nombre> - Cambiar a una seccion existente\n"
        "/delete <nombre> - Borrar una seccion\n"
        "Cualquier otro mensaje se envia a opencode dentro de la seccion activa."
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


def parse_opencode_output(text: str):
    session_id = ""
    chunks = []
    errors = []
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
        etype = event.get("type")
        if etype == "text":
            part = event.get("part") or {}
            t = part.get("text")
            if t:
                chunks.append(t)
        elif etype == "error":
            err = event.get("error") or {}
            name = str(err.get("name") or "Error")
            data = err.get("data") or {}
            msg = str(data.get("message") or "").strip()
            if msg:
                errors.append(name + ": " + msg)
    reply = "\n".join(chunks).strip()
    return session_id, reply, errors


def load_config() -> dict:
    """Read config.json (machine-specific). Returns {} on missing/invalid file."""
    if CONFIG_FILE.is_file():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("config.json invalido (%s); usando valores por defecto.", exc)
    return {}


def resolve_path(raw: str, label: str) -> Path:
    """Expand ~ and env vars, resolve relative paths against BASE_DIR and
    require the folder to exist. Raises RuntimeError with a clear message."""
    candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise RuntimeError("La ruta '" + label + "' no existe en disco: " + str(candidate))
    return candidate


def get_routes() -> dict[str, str]:
    routes = load_config().get("routes", {})
    if not isinstance(routes, dict):
        log.warning("config.json: 'routes' debe ser un objeto de nombre->path; ignorado.")
        return {}
    return {str(k): str(v) for k, v in routes.items() if str(v).strip()}


def get_default_route() -> str:
    return str(load_config().get("default_route", "")).strip()


def resolve_route(name: str) -> Path:
    routes = get_routes()
    if name not in routes:
        avail = ", ".join(sorted(routes)) or "(ninguna definida en config.json)"
        raise RuntimeError(
            "La ruta '" + name + "' no existe en config.json. Rutas disponibles: " + avail + "."
        )
    return resolve_path(routes[name], name)


def get_section_dir(route: str) -> Path:
    """Effective cwd for a section: the named route, or BASE_DIR when empty."""
    if route:
        return resolve_route(route)
    return BASE_DIR


async def _opencode_attempt(prompt: str, work_dir: Path, session_id: str, timeout: int, title: str):
    args = [OPENCODE_EXE, "run", "--format", "json", "--title", title]
    if session_id:
        args += ["--session", session_id]
    args.append(prompt)
    env = dict(os.environ)
    project_config = BASE_DIR / "opencode.json"
    if project_config.is_file():
        # Keep the bot's permission rules working no matter the work_dir:
        # opencode merges this file on top of whatever config the work_dir has.
        env["OPENCODE_CONFIG"] = str(project_config)
    log.info("Ejecutando opencode (session=%s, cwd=%s): %s", session_id or "<nueva>", work_dir, prompt[:80])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(work_dir),
        env=env,
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
    session_id_out, reply, errors = parse_opencode_output(out)
    return session_id_out, reply, errors, proc.returncode, out, err


async def run_opencode(prompt: str, work_dir: Path, session_id: str = "", timeout: int = 600, title: str = "telegram-bot") -> tuple[str, str]:
    # The provider sometimes returns an empty completion and opencode exits 0
    # without emitting text or an error event; retry once before giving up.
    sid = session_id
    session_id_out, reply, errors, returncode, out, err = await _opencode_attempt(prompt, work_dir, sid, timeout, title)
    if session_id_out:
        sid = session_id_out
    if not reply.strip() and not errors and not clean_output(err):
        log.warning("opencode devolvio una respuesta vacia (rc=%s); reintentando.", returncode)
        session_id_out, reply, errors, returncode, out, err = await _opencode_attempt(prompt, work_dir, sid, timeout, title)
        if session_id_out:
            sid = session_id_out
    if not reply.strip():
        if errors:
            reply = "\n".join(errors)
        elif clean_output(err):
            reply = clean_output(err)
        else:
            log.warning(
                "opencode termino sin generar texto ni error parseable (rc=%s). stdout: %s | stderr: %s",
                returncode,
                out[:500],
                err[:500],
            )
            reply = "Sin respuesta: opencode termino sin generar texto ni reportar error (se reintento una vez). Puede ser un problema del modelo/proveedor en uso (revisa tu cuenta y configuracion de opencode)."
    log.info("opencode termino (session=%s, reply_len=%d)", sid or "<nueva>", len(reply))
    return reply, sid


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
    reply = "Sin respuesta."
    try:
        section = get_current_section(chat_id)
        sec = get_section(chat_id, section)
        sid = sec.get("session", "")
        route = sec.get("route", "")
        try:
            work_dir = get_section_dir(route)
        except RuntimeError as exc:
            reply = "Error de ruta en la seccion '" + section + "': " + str(exc)
        else:
            stored_dir = sec.get("dir", "")
            if sid and stored_dir and str(work_dir) != stored_dir:
                reply = (
                    "La seccion '" + section + "' nacio en " + stored_dir
                    + " pero su ruta '" + route_label(route) + "' ahora apunta a " + str(work_dir)
                    + ". Corrige config.json o reinicia la seccion: /delete " + section
                    + " y /new " + section + " " + route
                )
            else:
                if stored_dir != str(work_dir):
                    set_section_dir(chat_id, section, str(work_dir))
                reply, new_sid = await run_opencode(prompt, work_dir, sid, title=section)
                if new_sid:
                    set_section_session(chat_id, section, new_sid)
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


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_chat.id):
        await update.message.reply_text(auth_error(update.effective_chat.id))
        return
    await update.message.reply_text("Deteniendo el bot...")
    await context.application.stop()


async def cmd_sections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_owner(chat_id):
        await update.message.reply_text(auth_error(chat_id))
        return
    chat_data = get_chat_sessions(chat_id)
    current = chat_data.get("current") or DEFAULT_SECTION
    lines = ["📚 Secciones (contextos):"]
    for name, sec in chat_data.get("sections", {}).items():
        marker = "➡️ " if name == current else "   "
        active = " (activa)" if name == current else ""
        route = sec.get("route", "")
        try:
            work_dir = str(get_section_dir(route))
            lines.append(marker + name + active + " - ruta: " + route_label(route) + " -> " + work_dir)
        except RuntimeError as exc:
            lines.append(marker + name + active + " - ruta: " + route_label(route) + " (INVALIDA: " + str(exc) + ")")
    lines.append("")
    lines.append("Usa /new <nombre> [ruta] para crear una seccion, /use <nombre> para cambiar, /delete <nombre> para borrar.")
    await update.message.reply_text("\n".join(lines))


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_owner(chat_id):
        await update.message.reply_text(auth_error(chat_id))
        return
    args = context.args or []
    route_arg = ""
    if not args:
        name = DEFAULT_SECTION + "-" + str(len(get_chat_sessions(chat_id).get("sections", {})) + 1)
    elif len(args) == 1:
        name = args[0].strip()
    elif len(args) == 2:
        name, route_arg = args[0].strip(), args[1].strip()
    else:
        await update.message.reply_text("Uso: /new <nombre> [ruta]. Usa /sections para ver tus secciones y sus rutas.")
        return
    if not name or " " in name:
        await update.message.reply_text("Nombre de seccion invalido (una sola palabra, sin espacios).")
        return
    if name in get_chat_sessions(chat_id).get("sections", {}):
        if route_arg:
            await update.message.reply_text(
                "La seccion '" + name + "' ya existe. Para cambiarle la ruta primero /delete " + name + "."
            )
            return
        switch_section(chat_id, name)
        await update.message.reply_text("La seccion '" + name + "' ya existia; la he activado.")
        return
    route = route_arg or get_default_route()
    try:
        work_dir = get_section_dir(route)
    except RuntimeError as exc:
        await update.message.reply_text("Error: " + str(exc))
        return
    create_section(chat_id, name, route, str(work_dir))
    await update.message.reply_text(
        "Seccion '" + name + "' creada y activada.\n"
        "Ruta: " + route_label(route) + " -> " + str(work_dir) + "\n"
        "El primer mensaje que envies iniciara su contexto en opencode."
    )


async def cmd_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_owner(chat_id):
        await update.message.reply_text(auth_error(chat_id))
        return
    if not context.args:
        await update.message.reply_text("Uso: /use <nombre>. Usa /sections para ver las disponibles.")
        return
    name = " ".join(context.args).strip()
    if switch_section(chat_id, name):
        sec = get_section(chat_id, name)
        route = sec.get("route", "")
        try:
            info = " (ruta: " + route_label(route) + " -> " + str(get_section_dir(route)) + ")"
        except RuntimeError as exc:
            info = " (ruta: " + route_label(route) + " INVALIDA: " + str(exc) + ")"
        await update.message.reply_text("Ahora estas en la seccion '" + name + "'." + info)
    else:
        await update.message.reply_text("No existe la seccion '" + name + "'.")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_owner(chat_id):
        await update.message.reply_text(auth_error(chat_id))
        return
    if not context.args:
        await update.message.reply_text("Uso: /delete <nombre>. Usa /sections para ver las disponibles.")
        return
    name = " ".join(context.args).strip()
    if delete_section(chat_id, name):
        await update.message.reply_text("Seccion '" + name + "' borrada.")
    else:
        await update.message.reply_text("No existe la seccion '" + name + "'.")


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


def get_commit_title() -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%s"],
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
    commit_hash = get_repo_commit()
    commit_title = get_commit_title()
    commit_info = f"{commit_title} ({commit_hash})"
    default_route = get_default_route()
    try:
        routes_info = (
            "default_route: " + (route_label(default_route) if default_route else "(sin default)")
            + " -> " + str(get_section_dir(default_route))
            + " | rutas definidas: " + str(len(get_routes()))
        )
    except RuntimeError as exc:
        routes_info = "default_route INVALIDA: " + str(exc)
    await update.message.reply_text(
        "📊 Bot state\n"
        "PID: " + str(os.getpid()) + "\n"
        "Last commit: " + commit_info + "\n"
        "Routes: " + routes_info + "\n"
        "Started: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(STARTED_AT)) + "\n"
        "Uptime: " + str(elapsed) + "s"
    )


def start_heartbeat() -> None:
    def _loop() -> None:
        while True:
            time.sleep(300)
            log.info("Bot vivo - %s", time.strftime("%H:%M"))

    threading.Thread(target=_loop, daemon=True).start()


def has_internet() -> bool:
    try:
        with socket.create_connection(("api.telegram.org", 443), timeout=5):
            return True
    except OSError:
        return False


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
    if "work_dir" in load_config():
        log.warning(
            "config.json: 'work_dir' fue eliminado y se ignora; "
            "definelo como ruta con nombre en 'routes' (ver config.example.json)."
        )
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("state", cmd_state))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("sections", cmd_sections))
    application.add_handler(CommandHandler("new", cmd_new))
    application.add_handler(CommandHandler("use", cmd_use))
    application.add_handler(CommandHandler("delete", cmd_delete))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    if not has_internet():
        log.error("No hay conexion a internet. El bot no puede arrancar.")
        sys.exit(
            "Error: no hay conexion a internet. "
            "Verifica tu conexion y vuelve a intentarlo."
        )
    start_heartbeat()
    log.info("Bot iniciado. Esperando mensajes...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except NetworkError as exc:
        log.error("No se pudo conectar con Telegram: %s", exc)
        sys.exit(
            "Error de red: no hay conexion a internet. "
            "Verifica tu conexion y vuelve a intentarlo."
        )


if __name__ == "__main__":
    main()