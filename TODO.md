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
