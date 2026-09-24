# TODO

- [ ] **Implementar `/plan` y `/build` en Telegram**: agregar comandos al bot para elegir el agente con el que corre opencode (`opencode run --agent plan` / `--agent build`), en vez de usar siempre el agente por defecto.
  - Guardar el agente elegido por chat (o por sección) en `sessions.json`.
  - `/state` debería mostrar el agente activo.
  - Actualizar `/help` y el README.

- [ ] **Implementar cola de mensajes**: si llegan 2 o más mensajes mientras opencode todavía está procesando, deben esperar su turno y procesarse de a uno, en orden de llegada.
  - Hoy cada mensaje lanza su propio `opencode run` en paralelo, y si dos caen en la misma sección pueden pisar la misma sesión.
  - Usar un `asyncio.Lock` o `asyncio.Queue` por chat (o por sección).
  - Avisar por Telegram cuando un mensaje queda en espera (ej: "En cola, hay N mensajes antes").

- [x] **Rutas predefinidas por sección**: rutas con nombre en `config.json` para que cada sección trabaje en su propia carpeta.
  - Diseño final: `work_dir` fue **eliminado** (era un solo mecanismo redundante con las rutas). `config.json` ahora tiene `routes` (nombre -> carpeta) y `default_route`.
  - `/new <nombre> [ruta]` crea la sección en esa ruta (valida que exista en `routes` y que la carpeta esté en disco; error claro si no). Nombre de una sola palabra.
  - `sessions.json` guarda por sección `{session, route, dir}`: el **nombre** de ruta (portable entre PCs con filesystems distintos, cada `config.json` local lo resuelve) y el directorio donde nació la sesión.
  - `run_opencode` recibe el `cwd` de la sección activa como parámetro.
  - `/sections` muestra la ruta de cada sección y `/use` avisa en qué ruta quedaste.
  - Si la ruta de una sección con sesión ahora apunta a otro directorio, el bot no ejecuta y da error claro (el dueño corrige config.json o hace /delete + /new).
  - `/help` y README actualizados.

## Sugerencias de la revision del proyecto (2026-09-23)

- [ ] **Restaurar `bot_status.ps1`**: el README y `agent/context.md` lo citan (chquear PID + commit), pero el script no existe ni fue commiteado nunca. Recrearlo o quitar las referencias.

- [ ] **Proteger `sessions.json` contra escrituras perdidas**: cada operacion hace load-modify-save sin lock y `write_text` no es atomico; dos mensajes concurrentes pueden pisarse entre si. Escribir via archivo temporal + `os.replace()` y serializar el acceso (complementa la cola de mensajes).

- [ ] **Rotacion de logs**: `bot.log` ya pasa los 3 MB y crece sin limite. Usar `logging.handlers.RotatingFileHandler` (ej: 5 MB x 3 backups).

- [ ] **Timeout de opencode configurable**: los 600s de `run_opencode` estan hardcodeados; moverlo a `config.json` (ej: `opencode_timeout`) y releerlo por mensaje, como `routes`.

- [ ] **Tests unitarios con pytest**: no hay ninguna prueba y `bot.py` tiene funciones puras faciles de testear: `parse_opencode_output`, `split_long`, `clean_output`, `_migrate_section`/`_migrate_chat`, `resolve_path`/`get_routes`.

- [ ] **CI minima (GitHub Actions)**: job que instale requirements y corra lint (ruff o pyflakes) + pytest en cada push/PR.

- [ ] **No exponer el `chat_id` del dueno en `auth_error`**: el mensaje de "No autorizado" revela el dueno registrado a cualquier chat desconocido; mostrar solo el chat_id del remitente.

- [ ] **Responder a mensajes no-texto**: hoy fotos/documentos/stickers se ignoran en silencio (el handler filtra solo TEXT). Contestar "solo texto por ahora" o descargar el archivo y pasarselo a opencode.

- [ ] **Corregir typo `chan_id` -> `chat_id`** en el mensaje de bienvenida de `/start`.
