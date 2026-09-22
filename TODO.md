# TODO

- [ ] **Implementar `/plan` y `/build` en Telegram**: agregar comandos al bot para elegir el agente con el que corre opencode (`opencode run --agent plan` / `--agent build`), en vez de usar siempre el agente por defecto.
  - Guardar el agente elegido por chat (o por sección) en `sessions.json`.
  - `/state` debería mostrar el agente activo.
  - Actualizar `/help` y el README.

- [ ] **Implementar cola de mensajes**: si llegan 2 o más mensajes mientras opencode todavía está procesando, deben esperar su turno y procesarse de a uno, en orden de llegada.
  - Hoy cada mensaje lanza su propio `opencode run` en paralelo, y si dos caen en la misma sección pueden pisar la misma sesión.
  - Usar un `asyncio.Lock` o `asyncio.Queue` por chat (o por sección).
  - Avisar por Telegram cuando un mensaje queda en espera (ej: "En cola, hay N mensajes antes").

- [ ] **Rutas predefinidas por sección**: definir en el archivo de configuración rutas con nombre y poder elegir cuál es la ruta por defecto, para que cada sección trabaje en su propia carpeta.
  - En `config.json` agregar algo como:
    ```json
    {
      "work_dir": ".",
      "routes": {
        "proyectoA": "E:/codigo/proyectoA",
        "proyectoB": "E:/codigo/proyectoB"
      },
      "default_route": "proyectoA"
    }
    ```
  - `default_route` indica la ruta en la que siempre empiezan las secciones nuevas (si no existe, se usa `work_dir` como hoy).
  - Extender `/new` para aceptar una ruta opcional: `/new <nombre> [ruta]`.
    - `/new api proyectoA` → crea la sección "api" trabajando en `E:/codigo/proyectoA`.
    - `/new api` → crea la sección "api" trabajando en la `default_route`.
  - Guardar la ruta de cada sección en `sessions.json` y que `run_opencode` la use como `cwd` al lanzar opencode.
  - `/sections` debería mostrar la ruta de cada sección, y `/use` podría avisar en qué ruta quedaste parado.
  - Validar que el nombre de ruta exista en `routes` y que la carpeta exista en disco (error claro si no).
  - Actualizar `/help` y el README.
