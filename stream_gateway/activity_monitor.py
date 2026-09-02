"""
activity_monitor.py — Visibilidad hacia MySQL de actividades actuales y ejecutadas.

Este módulo es puramente aditivo y de una sola vía (write-only, fire-and-forget):
NINGUNA rutina existente depende de él para funcionar. Si MySQL no está
configurado, está caído, o las tablas todavía no existen, el resto del
sistema sigue funcionando exactamente igual — este módulo nunca lanza
excepciones hacia quien lo llama y nunca bloquea el event loop.

Activación (todo apagado por defecto):
    ACTIVITY_MONITOR_ENABLED=true
    MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE

Tablas esperadas (ver sql/schema.sql):
    live_activities     (MEMORY)  — "Actividades actuales"
    activity_log        (InnoDB)  — "Actividades ejecutadas"
    activity_snapshots  (InnoDB)  — pulso global del proceso

Uso desde el resto del código:
    from stream_gateway import activity_monitor as monitor

    monitor.record_event(session, "answered")
    monitor.record_event_by_uuid(call_uuid, "stream_task_error", detail=str(exc))
    monitor.upsert_live(session)
    monitor.remove_live(call_uuid)
    await monitor.start_snapshot_loop()   # tarea de fondo, opcional en main.py
"""

import asyncio
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from utils.app_debugger import init_debugger

load_dotenv()

LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logger = init_debugger(LOG_FILE)

# ---------------------------------------------------------------------------
# Configuración — todo desactivado hasta que se configure explícitamente
# ---------------------------------------------------------------------------

ENABLED = os.getenv("ACTIVITY_MONITOR_ENABLED", "false").strip().lower() in ("1", "true", "yes")

MYSQL_HOST     = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER     = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "odio_monitoring")

MYSQL_POOL_MIN = int(os.getenv("MYSQL_POOL_MIN", "1"))
MYSQL_POOL_MAX = int(os.getenv("MYSQL_POOL_MAX", "5"))

ACTIVITY_SNAPSHOT_INTERVAL = float(os.getenv("ACTIVITY_SNAPSHOT_INTERVAL", "15"))

# Ventana mínima entre reintentos de conexión tras un fallo, para no
# inundar logs ni reintentar en cada evento si MySQL está caído.
_RETRY_BACKOFF_SECONDS = 30

_HOST_PID = os.getpid()

# ---------------------------------------------------------------------------
# Estado interno
# ---------------------------------------------------------------------------

_pool = None
_pool_lock: "asyncio.Lock | None" = None
_last_failure_ts: float = 0.0
_last_warning_ts: float = 0.0
_background_tasks: set = set()


def _epoch_to_dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch)


def _warn_throttled(message: str) -> None:
    """Loguea a nivel warning como máximo una vez por minuto, para no saturar
    el log de la aplicación si MySQL está caído/mal configurado."""
    global _last_warning_ts
    now = time.time()
    if now - _last_warning_ts >= 60:
        _last_warning_ts = now
        logger.warning(f"[MONITOR] {message}")


def _spawn(coro) -> None:
    """Dispara una coroutine en segundo plano sin esperar su resultado.
    Mantiene una referencia fuerte a la task para que no sea recolectada
    por el garbage collector a mitad de ejecución (gotcha típico de asyncio)."""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        # No hay event loop corriendo (p. ej. import fuera de asyncio.run) — no-op.
        coro.close()
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ---------------------------------------------------------------------------
# Pool de conexiones (aiomysql, perezoso)
# ---------------------------------------------------------------------------

async def _get_pool():
    global _pool, _pool_lock, _last_failure_ts

    if not ENABLED:
        return None

    if _pool is not None:
        return _pool

    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    async with _pool_lock:
        if _pool is not None:
            return _pool

        now = time.time()
        if now - _last_failure_ts < _RETRY_BACKOFF_SECONDS:
            return None

        try:
            import aiomysql
        except ImportError:
            _warn_throttled(
                "ACTIVITY_MONITOR_ENABLED=true pero el paquete 'aiomysql' no está "
                "instalado. Ejecuta: pip install -r requirements.txt"
            )
            _last_failure_ts = now
            return None

        try:
            _pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                db=MYSQL_DATABASE,
                minsize=MYSQL_POOL_MIN,
                maxsize=MYSQL_POOL_MAX,
                autocommit=True,
                connect_timeout=3,
                pool_recycle=280,
            )
            logger.info(
                f"[MONITOR] Pool MySQL inicializado ({MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE})"
            )
            return _pool
        except Exception as exc:
            _last_failure_ts = now
            _warn_throttled(f"No se pudo conectar a MySQL para monitoreo: {exc}")
            _pool = None
            return None


async def _execute(query: str, params: tuple) -> None:
    pool = await _get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
    except Exception as exc:
        _warn_throttled(f"Escritura de monitoreo falló ({query[:40]}...): {exc}")


# ---------------------------------------------------------------------------
# activity_log — "Actividades ejecutadas"
# ---------------------------------------------------------------------------

_LOG_INSERT_SQL = """
    INSERT INTO activity_log
        (call_uuid, event_type, state_after, call_type, agent_id,
         customer_name, coe_name, hangup_who, hangup_cause,
         bytes_rx, bytes_tx, duration_ms, detail, host_pid)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def log_event(
    call_uuid: str,
    event_type: str,
    *,
    state_after: str | None = None,
    call_type: str | None = None,
    agent_id: str | None = None,
    customer_name: str | None = None,
    coe_name: str | None = None,
    hangup_who: str | None = None,
    hangup_cause: int | None = None,
    bytes_rx: int | None = None,
    bytes_tx: int | None = None,
    duration_ms: int | None = None,
    detail: str | None = None,
) -> None:
    """Inserta un evento en activity_log. No bloqueante, no lanza excepciones."""
    if not ENABLED:
        return
    params = (
        call_uuid, event_type, state_after, call_type, agent_id,
        customer_name, coe_name, hangup_who, hangup_cause,
        bytes_rx, bytes_tx, duration_ms,
        (detail[:512] if detail else None), _HOST_PID,
    )
    _spawn(_execute(_LOG_INSERT_SQL, params))


def record_event(session, event_type: str, **extra) -> None:
    """Variante rica: extrae los campos relevantes directamente de una
    CallSession activa. Usar cuando la sesión todavía está disponible."""
    if not ENABLED or session is None:
        return
    try:
        info = session.customer_information or {}
        duration_ms = int((time.time() - session.created_at) * 1000)
        log_event(
            session.call_uuid,
            event_type,
            state_after=session.state,
            call_type=info.get("callType"),
            agent_id=session.agent_id or None,
            customer_name=info.get("customerName"),
            coe_name=info.get("coeName"),
            hangup_who=session.hangup_who,
            hangup_cause=session.hangup_cause,
            duration_ms=duration_ms,
            **extra,
        )
    except Exception as exc:
        _warn_throttled(f"record_event falló para {getattr(session, 'call_uuid', '?')}: {exc}")


def record_event_by_uuid(call_uuid: str, event_type: str, **extra) -> None:
    """Variante mínima: usar cuando ya no se tiene el objeto CallSession
    a mano (p. ej. la tarea de streaming terminó después de que la sesión
    fue removida de memoria)."""
    if not ENABLED or not call_uuid:
        return
    log_event(call_uuid, event_type, **extra)


# ---------------------------------------------------------------------------
# live_activities — "Actividades actuales"
# ---------------------------------------------------------------------------

_LIVE_UPSERT_SQL = """
    INSERT INTO live_activities
        (call_uuid, state, call_type, agent_id, customer_name, coe_name,
         hangup_who, hangup_cause, recording_segment,
         rx_queue_size, tx_queue_size, queue_maxsize, stream_task_alive,
         host_pid, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        state = VALUES(state),
        call_type = VALUES(call_type),
        agent_id = VALUES(agent_id),
        customer_name = VALUES(customer_name),
        coe_name = VALUES(coe_name),
        hangup_who = VALUES(hangup_who),
        hangup_cause = VALUES(hangup_cause),
        recording_segment = VALUES(recording_segment),
        rx_queue_size = VALUES(rx_queue_size),
        tx_queue_size = VALUES(tx_queue_size),
        queue_maxsize = VALUES(queue_maxsize),
        stream_task_alive = VALUES(stream_task_alive),
        host_pid = VALUES(host_pid),
        updated_at = VALUES(updated_at)
"""

_LIVE_DELETE_SQL = "DELETE FROM live_activities WHERE call_uuid = %s"


def upsert_live(session) -> None:
    """Refleja el estado actual de una CallSession en live_activities."""
    if not ENABLED or session is None:
        return
    try:
        info = session.customer_information or {}

        def _qsize(q):
            try:
                return q.qsize()
            except Exception:
                return None

        stream_task = session.stream_task
        stream_alive = bool(stream_task is not None and not stream_task.done())

        now = _epoch_to_dt(time.time())
        params = (
            session.call_uuid,
            session.state,
            info.get("callType"),
            session.agent_id or None,
            info.get("customerName"),
            info.get("coeName"),
            session.hangup_who,
            session.hangup_cause,
            session.recording_segment,
            _qsize(session.rx_queue),
            _qsize(session.tx_queue),
            session.rx_queue.maxsize if session.rx_queue else None,
            stream_alive,
            _HOST_PID,
            _epoch_to_dt(session.created_at),
            now,
        )
        _spawn(_execute(_LIVE_UPSERT_SQL, params))
    except Exception as exc:
        _warn_throttled(f"upsert_live falló para {getattr(session, 'call_uuid', '?')}: {exc}")


def remove_live(call_uuid: str) -> None:
    """Quita una llamada de live_activities (cierre limpio o purga por TTL)."""
    if not ENABLED or not call_uuid:
        return
    _spawn(_execute(_LIVE_DELETE_SQL, (call_uuid,)))


# ---------------------------------------------------------------------------
# activity_snapshots — pulso global del proceso
# ---------------------------------------------------------------------------

_SNAPSHOT_INSERT_SQL = """
    INSERT INTO activity_snapshots
        (sessions_total, sessions_ringing, sessions_answered, sessions_paused,
         sessions_finished, max_rx_queue, max_tx_queue, avg_rx_queue, avg_tx_queue,
         active_stream_tasks, pending_merge_tasks, asyncio_task_count,
         event_loop_lag_ms, host_pid)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


async def _measure_event_loop_lag() -> float | None:
    """Mide cuánto se retrasa el event loop en atender un callback ya
    agendado — proxy directo de saturación de CPU/recursos del proceso."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    fut: asyncio.Future = loop.create_future()
    loop.call_soon(lambda: fut.set_result(None) if not fut.done() else None)
    try:
        await asyncio.wait_for(fut, timeout=5)
    except asyncio.TimeoutError:
        return None
    return max(0.0, (loop.time() - start) * 1000)


async def _take_snapshot() -> None:
    from stream_gateway.session import sessions as live_sessions

    sessions_snapshot = list(live_sessions.values())

    by_state = {"ringing": 0, "answered": 0, "paused": 0, "finished": 0}
    rx_sizes, tx_sizes = [], []
    active_tasks = 0
    pending_merges = 0

    for sess in sessions_snapshot:
        by_state[sess.state] = by_state.get(sess.state, 0) + 1
        try:
            rx_sizes.append(sess.rx_queue.qsize())
            tx_sizes.append(sess.tx_queue.qsize())
        except Exception:
            pass
        if sess.stream_task is not None and not sess.stream_task.done():
            active_tasks += 1
        if sess._pending_merge_task is not None and not sess._pending_merge_task.done():
            pending_merges += 1

    lag_ms = await _measure_event_loop_lag()

    params = (
        len(sessions_snapshot),
        by_state.get("ringing", 0),
        by_state.get("answered", 0),
        by_state.get("paused", 0),
        by_state.get("finished", 0),
        max(rx_sizes) if rx_sizes else 0,
        max(tx_sizes) if tx_sizes else 0,
        (sum(rx_sizes) / len(rx_sizes)) if rx_sizes else 0.0,
        (sum(tx_sizes) / len(tx_sizes)) if tx_sizes else 0.0,
        active_tasks,
        pending_merges,
        len(asyncio.all_tasks()),
        lag_ms,
        _HOST_PID,
    )
    await _execute(_SNAPSHOT_INSERT_SQL, params)


async def start_snapshot_loop() -> None:
    """
    Tarea de fondo opcional: toma una foto del sistema completo cada
    ACTIVITY_SNAPSHOT_INTERVAL segundos. Pensada para correr junto a los
    servidores existentes vía asyncio.gather(...) en main.py.

    Si ACTIVITY_MONITOR_ENABLED es false, retorna de inmediato y no hace
    absolutamente nada — seguro de incluir siempre en el gather.
    """
    if not ENABLED:
        return

    logger.info(
        f"[MONITOR] Snapshot loop iniciado (cada {ACTIVITY_SNAPSHOT_INTERVAL}s)"
    )
    while True:
        try:
            await _take_snapshot()
        except Exception as exc:
            _warn_throttled(f"Fallo tomando snapshot del sistema: {exc}")
        await asyncio.sleep(ACTIVITY_SNAPSHOT_INTERVAL)
