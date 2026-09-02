# Monitoreo de actividades (MySQL)

Capa de **visibilidad** agregada sobre el servidor event-driven (`main.py`).
No reemplaza ni modifica el flujo de audio/control existente — solo observa
y escribe hacia MySQL en paralelo, en modo *fire-and-forget*. Pensada para
responder una pregunta concreta: **cuando el sistema se pone lento, ¿es por
recursos o por sesiones que quedan colgadas en memoria?**

## Diseño

Dos motores, cada uno para lo que sirve:

| Tabla | Engine | Qué responde |
|---|---|---|
| `live_activities` | **MEMORY** | "Actividades actuales" — foto en tiempo real de cada llamada activa. Todo en RAM: escritura sub-milisegundo, cero I/O de disco, se vacía si MySQL reinicia (correcto: es estado, no historia). |
| `activity_log` | **InnoDB** | "Actividades ejecutadas" — un renglón por evento de ciclo de vida (creada, contestada, colgada, expirada por TTL, error de streaming, etc.). Durable, indexado para reportes. |
| `activity_snapshots` | **InnoDB** | Pulso global del proceso cada N segundos: sesiones por estado, profundidad de colas de audio, cantidad de tareas asyncio vivas, lag del event loop. Esta es la tabla que separa "recursos" de "sesiones muertas". |

Ver [`sql/schema.sql`](sql/schema.sql) para el DDL completo (comentado) y
[`sql/reportes.sql`](sql/reportes.sql) para consultas de ejemplo listas para
correr.

## Cómo diagnosticar con esto

- **¿Sesiones muertas?** → `activity_snapshots.sessions_finished` (llamadas
  en estado `finished` que nunca recibieron `SEND_RECORDING`) creciendo de
  forma sostenida, o una tasa alta de `ttl_expired` en `activity_log`
  (consulta 2.3 de `reportes.sql`). Esto pasa cuando el agente/panel nunca
  dispara `SEND_RECORDING` y la sesión solo se libera al cumplirse el TTL
  (`SESSION_TTL_SECONDS`, default 3600s) — y hoy ese TTL solo se evalúa
  quando llega una llamada nueva (`cleanup_stale_sessions()` en
  `audio_socket_server.py`), así que en horas de tráfico bajo pueden
  acumularse sin que nada las note.
- **¿Recursos/latencia?** → `activity_snapshots.event_loop_lag_ms` alto,
  `max_rx_queue`/`max_tx_queue` cerca de `AUDIO_QUEUE_MAXSIZE`, o
  `stream_task_error` frecuente en `activity_log` (la tarea que reenvía
  audio a Odio está fallando/tardando — normalmente conectividad hacia
  `WSS_ODIO_URL`, no el servidor local).

## Activación

Todo apagado por defecto (`ACTIVITY_MONITOR_ENABLED=false` en `.env`) —
el código de monitoreo existe y se ejecuta, pero cada función corta en la
primera línea y no hace nada. Cero riesgo en el deploy actual.

Para activarlo:

1. Instalar la dependencia nueva: `pip install -r requirements.txt` (agrega
   `aiomysql`).
2. Crear la base y las tablas: `mysql -u root -p < sql/schema.sql`.
3. En `.env`, completar `MYSQL_USER` / `MYSQL_PASSWORD` (y host/puerto/db si
   no son los default) y poner `ACTIVITY_MONITOR_ENABLED=true`.
4. Reiniciar `main.py`.

Si MySQL no está disponible o las tablas no existen todavía, el módulo
loguea **una advertencia por minuto** (no satura el log) y sigue sin afectar
el flujo de llamadas — nunca lanza excepciones hacia el código que lo llama.

## Qué se instrumentó (y dónde)

Todo aditivo, ninguna línea de lógica existente fue alterada — solo se
agregaron llamadas a `stream_gateway/activity_monitor.py` en los puntos
donde el estado *ya* cambiaba:

- `stream_gateway/session.py` — `activate_streaming` (answered/resumed),
  `deactivate_streaming` (stream_deactivated), `signal_hangup` (finished),
  `cleanup_stale_sessions` (ttl_expired — la señal de sesión muerta).
- `stream_gateway/audio_socket_server.py` — creación de sesión (UUID
  recibido), señal de hangup a nivel de paquete TCP, cierre de conexión.
- `ws_agent_gateway/control_server.py` — hangup manual del agente,
  resultado de `SEND_RECORDING` (éxito/error), y el resultado de la tarea
  de streaming en vivo (`stream_task_done` / `stream_task_error` — la señal
  más directa de problemas de recursos/conectividad hacia Odio).
- `main.py` — se agregó `start_snapshot_loop()` como tercera tarea del
  `asyncio.gather(...)` junto a los dos servidores existentes. Si el flag
  está apagado, retorna de inmediato y no hace nada.

## Mantenimiento

`activity_log` y `activity_snapshots` (InnoDB) crecen sin límite. No hay
purga automática — correr manualmente o por cron, por ejemplo para
conservar 30 días:

```sql
DELETE FROM activity_log       WHERE created_at  < NOW() - INTERVAL 30 DAY;
DELETE FROM activity_snapshots WHERE captured_at < NOW() - INTERVAL 30 DAY;
```
