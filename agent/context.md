# Contexto del proyecto: opencode-telegram

> Documento de contexto para un agente de IA. Describe qué es el proyecto, cómo funciona, cómo configurarlo y cómo contribuir.

## Resumen

`opencode-telegram` es un bot de Telegram escrito en Python que actúa como puente entre un chat de Telegram y [opencode](https://opencode.ai). El usuario manda cualquier mensaje desde su teléfono y el bot lo ejecuta a través de `opencode run` (modo no interactivo) en la PC del propietario; la respuesta de opencode se limpia y se devuelve al chat en trozos de máximo 4096 caracteres (límite de Telegram).

Permite usar opencode y, por extensión, la PC del usuario, de forma remota desde el móvil.

## Estructura del repositorio

```
opencode-telegram/
├── bot.py            # Lógica principal del bot de Telegram
├── opencode.json     # Config de permisos de opencode (qué puede hacer el bot)
├── requirements.txt  # Dependencias de Python
├── README.md         # Documentación de instalación y uso
├── .env              # No versionado: TELEGRAM_TOKEN y OWNER_CHAT_ID
├── .gitignore        # Ignora .env, venv/, __pycache__/, *.log, sessions.json
├── sessions.json      # No versionado: mapa chat_id -> session_id de opencode
├── venv/             # Entorno virtual local (no versionado)
├── bot.log           # Logs de salida (no versionado)
└── bot.err.log       # Logs de errores (no versionado)
```

## Cómo funciona (`bot.py`)

1. **Arranque (`main`)**: lee `TELEGRAM_TOKEN` desde `.env`. Si falta, aborta. Registra handlers y hace `run_polling`.
2. **Autorización**: solo responde al chat cuyo `chat_id` coincide con `OWNER_CHAT_ID` guardado en `.env`. Si el dueño no está registrado (valor vacío), cualquier chat recibe "No hay dueno registrado. Envía /start para registrar este chat como dueno." Si hay dueño pero el chat no coincide, recibe "No autorizado. (Tu chat_id es X; el dueno registrado es Y.)" — esto ayuda a diagnosticar cuando se escribe desde un grupo/topic distinto al chat privado donde se registró el dueño. El mensaje se genera en el helper `auth_error(chat_id)`.
3. **Comandos**:
   - `/start`: si no hay owner registrado, guarda el `chat_id` actual como único autorizado (`OWNER_CHAT_ID`) usando `python-dotenv`. Si ya hay dueño y el chat no coincide, devuelve el mensaje de "No autorizado" con los chat_id (ver autorización).
   - `/ayuda`: lista comandos disponibles.
   - `/state`: estado completo (PID, último commit de git con el que arrancó, fecha de inicio y uptime en segundos). Solo dueño. Sirve para detectar una instancia desactualizada.
   - `/reset`: reinicio total del bot. Borra `OWNER_CHAT_ID` de `.env`, vacía `sessions.json` y relanza el proceso con `os.execv`. Solo lo puede usar el dueño.
4. **Mensajes normales (`handle_message`)**: manda un "Pensando...", llama a opencode, borra el "Pensando..." y responde con la salida.
5. **Indicadores de estado**: al arrancar (tras `acquire_single_instance`) captura `STARTED_AT` y `RUN_COMMIT` (commit de git vía `get_repo_commit()`, con fallback `"unknown"`) y escribe `.bot.pid` con 3 líneas: `<pid>`, `commit=<hash>`, `started=<fecha>`; registra `atexit` para borrarlo; en `/reset` lo borra antes del `os.execv` (el proceso reemplazado lo reescribe). Un hilo en segundo plano (`start_heartbeat`, daemon) registra cada 5 min `Bot vivo - HH:MM` en consola y `bot.log` (heartbeat). `/state` da el reporte completo (PID, último commit, inicio, uptime en segundos) para detectar instancias desactualizadas. `bot_status.ps1` (Windows) lee `.bot.pid`, verifica que el PID vive y compara el commit con `git rev-parse --short HEAD`, reportando vivo-actualizado / vivo-desactualizado / detenido. (.bot.pid solo es una "notita con el número"; el mutex/file-lock es quien impone la instancia única, por eso se escribe tras adquirir el candado.)
5. **Ejecución de opencode (`run_opencode`)**: lanza `opencode run --format json --title telegram-bot <prompt>` como subproceso async (`asyncio.create_subprocess_exec`), con timeout de 600s. Si se le pasa un `session_id`, añade `--session <id>` para continuar la conversación; si no, crea una sesión nueva. Devuelve `(reply, session_id)`. Si el parseo JSON no produce texto, hace fallback al stderr limpio.
6. **Resolución del ejecutable (`find_opencode_exe`)**: en Windows npm instala opencode como shim `.cmd` no ejecutable directo; el bot busca el `.exe` real en `%APPDATA%\npm\node_modules/opencode-ai/bin/opencode.exe`. En otros SO usa `shutil.which("opencode")`.
7. **Parseo JSON (`parse_opencode_output`)**: recorre las líneas JSON (ndjson); extrae `sessionID` y el texto de los eventos `type:"text"` (`part.text`). Une el texto y devuelve `(session_id, reply)`.
8. **Limpieza (`clean_output`)**: quita códigos ANSI y colapsa espacios/tabs. (Fallback si el JSON no trae texto.)
9. **Particionado (`split_long`)**: divide la respuesta en chunks de 4096 chars cortando por saltos de línea.
10. **Sesiones por chat**: `sessions.json` mapea `chat_id -> session_id` (`load_sessions`/`save_sessions`/`get_session_id`/`set_session_id`/`clear_sessions`). `handle_message` recupera el session del chat, lo pasa a `run_opencode` y guarda el nuevo.
11. **Reinicio (`cmd_reset`)**: solo dueño (si el chat no es dueño, devuelve el mensaje de "No autorizado" con los chat_id); borra `OWNER_CHAT_ID` y vacía `sessions.json`, avisa y relanza el proceso con `os.execv`.

## Configuración de permisos (`opencode.json`)

Como `opencode run` es no interactivo, los permisos se auto-rechazan. El archivo define qué puede hacer sin preguntar:
- `external_directory`: `*` = ask, pero `E:\` y `E:\**` = allow (solo lectura a la unidad E).
- `bash`: solo comandos de consulta permitidos: `Get-ChildItem*`, `ls*`, `dir*`, `Test-Path*`, `where*`.

Para ampliar capacidades del bot hay que ajustar estas reglas.

## Requisitos e instalación

- Python 3.10+
- opencode instalado en el sistema (vía npm)
- Un bot de Telegram creado con @BotFather

```powershell
git clone <repo-url>
cd opencode-telegram
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Crear `.env`:
```
TELEGRAM_TOKEN=token_de_botfather
```
(`OWNER_CHAT_ID` se autogenera al ejecutar `/start` la primera vez.)

Ejecutar:
```powershell
python -u bot.py
```

## Notas operativas / troubleshooting

- **`[WinError 2]`**: shim npm de opencode en Windows; el bot lo resuelve solo, pero hay que reiniciar el bot tras actualizar opencode.
- **Permisos rechazados**: el proceso es no interactivo; añadir la regla correspondiente a `opencode.json`.
- Logs en `bot.log` / `bot.err.log`.
- **Trampa de `.bot.lock` en Linux/macOS (futuro)**: en Windows la instancia única usa un mutex del sistema (`Global\OpenBotTelegramInstance`) que el SO suelta solo al morir el proceso, así que no hay problema. Pero en Linux/macOS el candado es un file-lock sobre `.bot.lock`: si el bot muere de golpe (kill -9, corte de luz) el candado puede quedar sin liberar y el archivo persiste, haciendo que el próximo arranque falle con "Ya hay una instancia del bot corriendo". Solución: borrar `.bot.lock` manualmente y relanzar. Esta nota es para cuando se programe en una PC Linux; en Windows actual no aplica. (.bot.lock ya está en `.gitignore`.)
- **Instancia desactualizada (stale)**: como `.bot.pid` guarda el commit de git con el que arrancó el bot, si se editan archivos o se hace pull sin reiniciar, la instancia viva queda corriendo código viejo (como le pasó al dueño). Para detectarlo: comparar la línea `commit=` de `.bot.pid` con `git rev-parse --short HEAD`, o ejecutar `bot_status.ps1` (Windows), que además confirma que el PID sigue vivo. Si difieren → reiniciar el bot (`/reset` o Ctrl+C + relanzar).

## Convenciones para contribuir

- No commitear `.env`, `venv/`, `__pycache__/`, `*.log`, `sessions.json` (ya en `.gitignore`).
- El bot solo debe ejecutarse en la máquina del propietario.
- Mantener el límite de 4096 caracteres por mensaje de Telegram.
- **Los mensajes de commit deben estar en inglés** (todos, sin excepción).
