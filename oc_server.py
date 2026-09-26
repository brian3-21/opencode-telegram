"""Cliente del servidor HTTP de opencode (`opencode serve`).

Sustituye a la invocacion por subproceso `opencode run --format json`. Con
opencode 1.16.x ese comando terminaba con frecuencia en rc=0 emitiendo solo el
evento `step_start`, sin ningun evento `text`, pese a que el modelo si habia
respondido y la respuesta habia quedado guardada en la base de datos de
opencode. El bot no tenia forma de verla y respondia "Sin respuesta".

El servidor expone la misma conversacion por HTTP de forma sincrona
(`POST /session/{id}/message` devuelve el mensaje del asistente completo) y por
un stream SSE (`GET /event`) que permite denegar las peticiones de permiso,
que en modo servidor quedan esperando en lugar de autodenegarse.
"""

import asyncio
import json
import os
import re

import httpx

DEFAULT_TIMEOUT = 600
STARTUP_TIMEOUT = 90
RECONNECT_DELAY = 2.0
LISTEN_RE = re.compile(r"listening on\s+(https?://[^\s]+)")


def same_dir(a: str, b: str) -> bool:
    """Compara dos carpetas tolerando barras y mayusculas.

    opencode normaliza las rutas con separadores nativos, asi que la carpeta
    que el bot resuelve (Path -> backslashes en Windows) puede no coincidir
    literalmente con la que devuelve la API.
    """
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (OSError, ValueError):
        return a.rstrip("\\/").lower() == b.rstrip("\\/").lower()


class OpencodeError(RuntimeError):
    """Error de la API de opencode o del arranque del servidor."""


class OpencodeServer:
    """Arranca y gobierna un unico `opencode serve` compartido por todas las secciones.

    Un solo proceso servidor atiende todas las secciones: cada sesion se crea
    con su propio `directory`, asi que las rutas de `config.json` siguen
    funcionando igual que con el subproceso por mensaje.
    """

    def __init__(
        self,
        exe: str,
        env: dict | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        log=None,
    ) -> None:
        self.exe = exe
        self.env = dict(env or {})
        self.host = host
        self.requested_port = port
        self.log = log
        self.base_url = ""
        self.port = port
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._listener: asyncio.Task | None = None
        self._events_ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self.permissions_rejected = 0
        self.last_error = ""

    # ------------------------------------------------------------------ logs

    def _debug(self, msg: str) -> None:
        if self.log:
            self.log.debug(msg)

    def _warn(self, msg: str) -> None:
        self.last_error = msg
        if self.log:
            self.log.warning(msg)

    # ------------------------------------------------------------- ciclo de vida

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Arranca el servidor y espera a que responda /global/health."""
        if self.running:
            return
        try:
            await self._spawn()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=15.0),
            )
            self._events_ready.clear()
            self._listener = asyncio.create_task(self._event_loop())
            await self._wait_healthy()
            # Sin el stream de eventos conectado se perderian las peticiones de
            # permiso y opencode se quedaria esperando una respuesta que nadie
            # da, colgando la seccion hasta el timeout.
            try:
                await asyncio.wait_for(self._events_ready.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                self._warn(
                    "El stream de eventos de opencode no conecta; los permisos "
                    "podrian colgarse. El bot sigue funcionando."
                )
        except Exception:
            await self.stop()
            raise

    async def _spawn(self) -> None:
        args = [
            self.exe,
            "serve",
            "--hostname",
            self.host,
            "--port",
            str(self.requested_port),
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self.env,
        )
        self.base_url = await asyncio.wait_for(
            self._read_listening_url(), timeout=STARTUP_TIMEOUT
        )
        if self.log:
            self.log.info("opencode serve escuchando en %s", self.base_url)

    async def _read_listening_url(self) -> str:
        """Lee stdout hasta que opencode anuncia el puerto que le ha tocado."""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                code = self._proc.returncode
                raise OpencodeError(
                    "opencode serve termino al arrancar (rc=%s)." % code
                )
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._debug("serve: " + line)
            match = LISTEN_RE.search(line)
            if match:
                url = match.group(1).rstrip("/")
                parsed = re.match(r"https?://[^:]+:(\d+)", url)
                if parsed:
                    self.port = int(parsed.group(1))
                return url

    async def _wait_healthy(self) -> None:
        assert self._client is not None
        deadline = asyncio.get_event_loop().time() + STARTUP_TIMEOUT
        last = ""
        while asyncio.get_event_loop().time() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                raise OpencodeError(
                    "opencode serve termino durante el arranque (rc=%s)."
                    % self._proc.returncode
                )
            try:
                resp = await self._client.get("/global/health", timeout=10.0)
                if resp.status_code == 200:
                    return
                last = "HTTP %s" % resp.status_code
            except Exception as exc:  # noqa: BLE001 - aún no está listo
                last = type(exc).__name__
            await asyncio.sleep(0.4)
        raise OpencodeError("opencode serve no respondio a tiempo (%s)." % last)

    async def ensure_running(self) -> None:
        """Reinicia el servidor si se murio (crash, reinicio de la PC, etc.)."""
        if self.running:
            return
        if self.log:
            self.log.warning("El servidor opencode no esta vivo; se reinicia.")
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        self._events_ready.clear()
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - el listener ya no importa
                pass
            self._listener = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.base_url = ""

    # ---------------------------------------------------------------- permisos

    async def _event_loop(self) -> None:
        """Escucha el stream SSE y rechaza automaticamente los permisos.

        En modo servidor opencode no se autodeniega (como hacia `opencode run`):
        se queda esperando a que alguien conteste. Sin este listener cualquier
        operacion que necesite permiso bloquearia la seccion hasta el timeout.
        """
        while True:
            try:
                assert self._client is not None
                async with self._client.stream(
                    "GET", "/event", timeout=httpx.Timeout(None)
                ) as resp:
                    if resp.status_code != 200:
                        self._warn("Stream de eventos: HTTP %s" % resp.status_code)
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue
                    self._debug("Escuchando eventos de opencode.")
                    self._events_ready.set()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        await self._handle_event(line[5:].strip())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconexión
                self._debug(
                    "Stream de eventos caido (%s); reintentando." % type(exc).__name__
                )
            await asyncio.sleep(RECONNECT_DELAY)

    async def _handle_event(self, payload: str) -> None:
        if not payload:
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        etype = str(event.get("type") or "")
        if not etype.startswith("permission.") or "asked" not in etype:
            return
        props = event.get("properties") or {}
        request_id = props.get("id") or props.get("requestID")
        if not request_id:
            return
        self.permissions_rejected += 1
        kind = props.get("permission") or "desconocido"
        if self.log:
            self.log.info("Permiso denegado automaticamente: %s (%s)", kind, request_id)
        try:
            assert self._client is not None
            await self._client.post(
                "/permission/%s/reply" % request_id,
                json={
                    "reply": "reject",
                    "message": "Denegado automaticamente: el bot opera sin "
                    "interaccion. Ajusta opencode.json si necesitas permitirlo.",
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("No se pudo denegar el permiso %s: %s" % (request_id, exc))

    # ----------------------------------------------------------------- sesiones

    async def _request(self, method: str, url: str, **kwargs):
        assert self._client is not None
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise OpencodeError(
                "opencode %s %s -> HTTP %s: %s"
                % (method, url, resp.status_code, resp.text[:300])
            )
        return resp

    async def create_session(self, directory: str, title: str) -> str:
        """Crea una sesion nueva y devuelve su id."""
        resp = await self._request(
            "POST", "/session", params={"directory": directory}, json={"title": title}
        )
        session_id = str((resp.json() or {}).get("id") or "")
        if not session_id:
            raise OpencodeError("opencode no devolvio un id de sesion.")
        if self.log:
            self.log.info("Sesion creada %s en %s", session_id, directory)
        return session_id

    async def session_exists(self, session_id: str, directory: str) -> bool:
        """Comprueba que la sesion guardada sigue existiendo en el servidor."""
        if not session_id:
            return False
        try:
            resp = await self._request(
                "GET",
                "/session/%s" % session_id,
                params={"directory": directory},
                timeout=30.0,
            )
        except OpencodeError as exc:
            if "HTTP 404" in str(exc):
                return False
            raise
        data = resp.json() or {}
        stored = str(data.get("directory") or "")
        if stored and not same_dir(stored, directory):
            if self.log:
                self.log.warning(
                    "La sesion %s pertenece a %s, no a %s", session_id, stored, directory
                )
            return False
        return True

    # ------------------------------------------------------------------ prompt

    async def prompt(
        self,
        directory: str,
        session_id: str,
        text: str,
        title: str = "telegram",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[str, str]:
        """Envia el texto y devuelve (respuesta, session_id)."""
        async with self._lock:
            sid = session_id
            if not sid or not await self.session_exists(sid, directory):
                sid = await self.create_session(directory, title)
            try:
                data = await self._send(directory, sid, text, timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # El servidor puede haberse caido: se reintenta una vez con el
                # servidor sano, que es lo unico que no se arregla solo.
                self._warn("Fallo de transporte contra opencode: %s" % exc)
                await self.ensure_running()
                data = await self._send(directory, sid, text, timeout)
            return self._read_answer(data), sid

    async def _send(self, directory: str, session_id: str, text: str, timeout: int) -> dict:
        resp = await self._request(
            "POST",
            "/session/%s/message" % session_id,
            params={"directory": directory},
            json={"parts": [{"type": "text", "text": text}]},
            timeout=httpx.Timeout(timeout, connect=15.0),
        )
        return resp.json() or {}

    @staticmethod
    def _read_answer(data: dict) -> str:
        """Saca el texto del asistente y, si la hubiera, el error real."""
        info = data.get("info") or {}
        chunks: list[str] = []
        errors: list[str] = []
        for part in data.get("parts") or []:
            ptype = part.get("type")
            if ptype == "text" and part.get("text"):
                chunks.append(str(part["text"]).strip())
            elif ptype in ("tool", "retry"):
                state = part.get("state") or {}
                if state.get("error"):
                    errors.append(str(state["error"]))
        if chunks:
            return "\n".join(c for c in chunks if c).strip()
        message = ""
        error = info.get("error")
        if isinstance(error, dict):
            data_ = error.get("data") or {}
            message = str(data_.get("message") or error.get("name") or "").strip()
        elif error:
            message = str(error).strip()
        if message:
            return "Error de opencode: " + message
        if errors:
            return "Error de opencode: " + "; ".join(dict.fromkeys(errors))
        return ""

    def status(self) -> str:
        if not self.running:
            return "detenido"
        return "activo en %s (puerto %s)" % (self.base_url, self.port)
