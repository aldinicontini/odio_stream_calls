-- ============================================================================
-- reportes.sql — Consultas de ejemplo sobre el esquema de sql/schema.sql
-- ============================================================================
-- Copiar/pegar en tu cliente MySQL favorito (Workbench, DBeaver, Grafana con
-- datasource MySQL, phpMyAdmin, etc.). Todas son de solo lectura.
-- ============================================================================


-- ============================================================================
-- 1) ACTIVIDADES ACTUALES  (tabla live_activities, ENGINE=MEMORY)
-- ============================================================================

-- 1.1 Foto completa de lo que está pasando ahora mismo
SELECT call_uuid, state, call_type, agent_id, customer_name,
       rx_queue_size, tx_queue_size, queue_maxsize,
       stream_task_alive, created_at, updated_at,
       TIMESTAMPDIFF(SECOND, created_at, NOW()) AS antiguedad_seg
FROM live_activities
ORDER BY updated_at DESC;

-- 1.2 Conteo por estado — vista rápida de carga actual
SELECT state, COUNT(*) AS total
FROM live_activities
GROUP BY state;

-- 1.3 🔴 Sesiones "muertas": quedaron en finished pero nunca llegó
--     SEND_RECORDING (nadie las sacó de memoria). Si esta lista crece con
--     el tiempo, el problema es manejo de sesiones, no recursos.
SELECT call_uuid, agent_id, customer_name, updated_at,
       TIMESTAMPDIFF(MINUTE, updated_at, NOW()) AS minutos_colgada
FROM live_activities
WHERE state = 'finished'
  AND updated_at < NOW() - INTERVAL 5 MINUTE
ORDER BY updated_at ASC;

-- 1.4 🟠 Backpressure de audio: colas cerca del límite (AUDIO_QUEUE_MAXSIZE).
--     Si esto aparece seguido, el problema es de recursos/latencia hacia
--     Odio (el consumidor no da abasto), no de sesiones colgadas.
SELECT call_uuid, agent_id, rx_queue_size, tx_queue_size, queue_maxsize, updated_at
FROM live_activities
WHERE queue_maxsize > 0
  AND (rx_queue_size >= queue_maxsize * 0.8 OR tx_queue_size >= queue_maxsize * 0.8)
ORDER BY updated_at DESC;

-- 1.5 Llamadas contestadas cuya tarea de streaming ya no está viva
--     (posible cuelgue silencioso del lado del streaming a Odio)
SELECT call_uuid, agent_id, updated_at
FROM live_activities
WHERE state = 'answered' AND stream_task_alive = 0;


-- ============================================================================
-- 2) ACTIVIDADES EJECUTADAS  (tabla activity_log, ENGINE=InnoDB)
-- ============================================================================

-- 2.1 Últimos eventos, cronológico
SELECT created_at, call_uuid, event_type, agent_id, detail
FROM activity_log
ORDER BY created_at DESC
LIMIT 200;

-- 2.2 Volumen de llamadas por día y por tipo de cierre — ¿cuántas terminan
--     "limpio" (send_recording_ok) vs. "muertas" (ttl_expired)?
SELECT DATE(created_at) AS dia,
       SUM(event_type = 'send_recording_ok') AS cerradas_ok,
       SUM(event_type = 'ttl_expired')       AS expiradas_por_ttl,
       SUM(event_type = 'send_recording_error') AS error_envio_metadata,
       SUM(event_type = 'stream_task_error') AS errores_streaming
FROM activity_log
WHERE created_at >= NOW() - INTERVAL 14 DAY
GROUP BY dia
ORDER BY dia DESC;

-- 2.3 Tasa de "sesión muerta" (ttl_expired) vs. total de llamadas — si esta
--     proporción crece en el tiempo, confirma un problema de sesiones
--     huérfanas (agentes/paneles que no disparan SEND_RECORDING).
SELECT
    SUM(event_type = 'ttl_expired') AS expiradas,
    SUM(event_type = 'created')     AS creadas,
    ROUND(100 * SUM(event_type = 'ttl_expired') / NULLIF(SUM(event_type = 'created'), 0), 2) AS pct_muertas
FROM activity_log
WHERE created_at >= NOW() - INTERVAL 7 DAY;

-- 2.4 Duración de llamada (created -> finished) por agente, últimos 7 días
SELECT agent_id,
       COUNT(*)                                  AS llamadas,
       ROUND(AVG(duration_ms) / 1000, 1)          AS duracion_prom_seg,
       ROUND(MAX(duration_ms) / 1000, 1)          AS duracion_max_seg
FROM activity_log
WHERE event_type = 'finished'
  AND created_at >= NOW() - INTERVAL 7 DAY
  AND agent_id IS NOT NULL AND agent_id <> ''
GROUP BY agent_id
ORDER BY llamadas DESC;

-- 2.5 Errores recientes de streaming hacia Odio (posible causa de "colas")
SELECT created_at, call_uuid, agent_id, detail
FROM activity_log
WHERE event_type = 'stream_task_error'
  AND created_at >= NOW() - INTERVAL 24 HOUR
ORDER BY created_at DESC;

-- 2.6 Traza completa de una llamada puntual (debugging dirigido)
SELECT created_at, event_type, state_after, detail, bytes_rx, bytes_tx, duration_ms
FROM activity_log
WHERE call_uuid = 'REEMPLAZAR-CON-EL-CALL-UUID'
ORDER BY created_at ASC;


-- ============================================================================
-- 3) PULSO DEL SISTEMA (tabla activity_snapshots, ENGINE=InnoDB)
-- ============================================================================

-- 3.1 Últimas 4 horas: sesiones activas, colas, tareas y lag del event loop
--     en una sola vista — la comparación clave para diagnosticar.
SELECT captured_at,
       sessions_total, sessions_answered, sessions_finished,
       max_rx_queue, max_tx_queue,
       active_stream_tasks, asyncio_task_count,
       event_loop_lag_ms
FROM activity_snapshots
WHERE captured_at >= NOW() - INTERVAL 4 HOUR
ORDER BY captured_at DESC;

-- 3.2 🟠 Señal de RECURSOS: picos de lag del event loop o colas llenas
SELECT captured_at, event_loop_lag_ms, max_rx_queue, max_tx_queue, asyncio_task_count
FROM activity_snapshots
WHERE captured_at >= NOW() - INTERVAL 24 HOUR
  AND (event_loop_lag_ms > 200 OR max_rx_queue > 400 OR max_tx_queue > 400)
ORDER BY captured_at DESC;

-- 3.3 🔴 Señal de SESIONES MUERTAS: sessions_finished (pendientes de
--     SEND_RECORDING) creciendo de forma sostenida en vez de oscilar cerca
--     de 0 — indica acumulación, no picos de tráfico.
SELECT captured_at, sessions_total, sessions_finished, asyncio_task_count
FROM activity_snapshots
WHERE captured_at >= NOW() - INTERVAL 24 HOUR
ORDER BY captured_at ASC;
