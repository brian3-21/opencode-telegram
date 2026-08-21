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
├── .gitignore        # Ignora .env, venv/, __pycache__/, *.log
├── venv/             # Entorno virtual local (no versionado)
├── bot.log           # Logs de salida (no versionado)
└── bot.err.log       # Logs de errores (no versionado)
```

## Cómo funciona (`bot.py`)

1. **Arranque (`main`)**: lee `TELEGRAM_TOKEN` desde `.env`. Si falta, aborta. Registra handlers y hace `run_polling`.
2. **Autorización**: solo responde al chat cuyo `chat_id` coincide con `OWNER_CHAT_ID` guardado en `.env`. Cualquier otro chat recibe "No autorizado."
3. **Comandos**:
   - `/start`: si no hay owner registrado, guarda el `chat_id` actual como único autorizado (`OWNER_CHAT_ID`) usando `python-dotenv`.
   - `/ayuda`: lista comandos disponibles.
4. **Mensajes normales (`handle_message`)**: manda un "Pensando...", llama a opencode, borra el "Pensando..." y responde con la salida.
5. **Ejecución de opencode (`run_opencode`)**: lanza `opencode run --title telegram-bot <prompt>` como subproceso async (`asyncio.create_subprocess_exec`), con timeout de 600s. Si no hay stdout usa stderr.
6. **Resolución del ejecutable (`find_opencode_exe`)**: en Windows npm instala opencode como shim `.cmd` no ejecutable directo; el bot busca el `.exe` real en `%APPDATA%\npm\node_modules/opencode-ai/bin/opencode.exe`. En otros SO usa `shutil.which("opencode")`.
7. **Limpieza (`clean_output`)**: quita códigos ANSI y colapsa espacios/tabs.
8. **Particionado (`split_long`)**: divide la respuesta en chunks de 4096 chars cortando por saltos de línea.

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

## Convenciones para contribuir

- No commitear `.env`, `venv/`, `__pycache__/`, `*.log` (ya en `.gitignore`).
- El bot solo debe ejecutarse en la máquina del propietario.
- Mantener el límite de 4096 caracteres por mensaje de Telegram.
