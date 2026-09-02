-- ============================================================================
-- schema.sql — Visibilidad de actividades (odio_stream_calls)
-- ============================================================================
--
-- Estrategia de motores:
--
--   MEMORY  -> live_activities        "Actividades actuales" (foto del ahora)
--   InnoDB  -> activity_log           "Actividades ejecutadas" (auditoría durable)
--   InnoDB  -> activity_snapshots     Pulso del sistema completo (recursos vs colas)
--
-- Por qué esta combinación:
--
--  - live_activities (MEMORY): se reescribe constantemente (una fila por
--    llamada activa, upsert en cada cambio de estado). Al vivir 100% en RAM,
--    el costo de escritura es despreciable y nunca compite por I/O de disco
--    con la grabación de audio. Es "state", no historia: si MySQL se
--    reinicia, la tabla queda vacía — correcto, porque el proceso Python
--    también habrá perdido ese estado en memoria.
--
--  - activity_log (InnoDB): un registro por evento de ciclo de vida
--    (created, answered, paused, finished, ttl_expired, send_recording_ok/
--    error, stream_task_done/error). Durable ante caídas, con row-level
--    locking (los inserts de distintas llamadas no se bloquean entre sí) e
--    índices para reportes históricos.
--
--  - activity_snapshots (InnoDB): una fila cada N segundos con el pulso
--    global del proceso (conteo de sesiones por estado, profundidad de
--    colas, lag del event loop). Esta es la tabla que responde la pregunta
--    "¿es recursos o son sesiones muertas?": lag/colas altas -> recursos;
--    sessions_finished_pending o ttl_expired creciendo -> sesiones muertas.
--
-- Ninguna de estas tablas es leída por la aplicación en su flujo normal:
-- solo se escribe en ellas (fire-and-forget, nunca bloqueante). Son
-- unidireccionales hacia el reporting/dashboard.
--
-- Ejecutar con un usuario que tenga CREATE en la base de datos elegida.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS odio_monitoring
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE odio_monitoring;

-- ----------------------------------------------------------------------------
-- live_activities — "Actividades actuales"
-- ----------------------------------------------------------------------------
-- Una fila por call_uuid activo. Se sobreescribe con INSERT ... ON DUPLICATE
-- KEY UPDATE en cada transición de estado. Se borra explícitamente cuando la
-- llamada se finaliza (SEND_RECORDING) o cuando el barrido de TTL la reclama.
--
-- ⚠️ MEMORY: sin TEXT/BLOB, sin FK, se vacía en cada restart de mysqld — es
-- el comportamiento deseado para una tabla de "estado actual".
-- Dimensiona max_heap_table_size con margen (cada fila pesa unos pocos KB;
-- miles de llamadas concurrentes son irrelevantes en tamaño).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS live_activities (
    call_uuid           VARCHAR(128)  NOT NULL,
    state               VARCHAR(20)   NOT NULL,   -- ringing | answered | paused | finished
    call_type           VARCHAR(10)   NULL,       -- inbound | outbound
    agent_id            VARCHAR(128)  NULL,
    customer_name       VARCHAR(255)  NULL,
    coe_name            VARCHAR(128)  NULL,
    hangup_who          VARCHAR(20)   NULL,
    hangup_cause        SMALLINT      NULL,
    recording_segment   SMALLINT      NULL,
    rx_queue_size       SMALLINT      NULL,       -- profundidad actual de la cola de audio RX
    tx_queue_size       SMALLINT      NULL,       -- profundidad actual de la cola de audio TX
    queue_maxsize       SMALLINT      NULL,       -- AUDIO_QUEUE_MAXSIZE vigente al momento del write
    stream_task_alive   TINYINT(1)    NULL,       -- ¿run_both_live sigue corriendo?
    host_pid            INT           NULL,       -- PID del proceso main.py que reportó
    created_at          DATETIME      NOT NULL,
    updated_at          DATETIME      NOT NULL,
    PRIMARY KEY (call_uuid),
    KEY idx_state (state) USING BTREE,
    KEY idx_agent (agent_id) USING BTREE,
    KEY idx_updated_at (updated_at) USING BTREE
) ENGINE=MEMORY DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- activity_log — "Actividades ejecutadas"
-- ----------------------------------------------------------------------------
-- Append-only. Un renglón por evento de ciclo de vida de cada llamada.
-- event_type esperados (no forzado por CHECK para no acoplar la app al
-- esquema — cualquier string nuevo simplemente aparece en los reportes):
--
--   created              UUID recibido en AudioSocket, sesión registrada
--   answered             Primer ANSWER del agente (streaming activado)
--   resumed              ANSWER tras una pausa previa (segundo segmento+)
--   stream_deactivated   Sinks -> NullSink, segmento de grabación cerrado
--                        (ocurre en pausa manual, hangup TCP y en el hangup
--                        final — mirar state_after / evento siguiente)
--   manual_hangup        Agente colgó desde el panel de control (WS)
--   tcp_hangup_signal    Paquete HANGUP recibido en el socket AudioSocket
--   finished             Conexión TCP de AudioSocket cerrada definitivamente
--   ttl_expired          Sesión purgada por cleanup_stale_sessions (⚠️ señal
--                        de "sesión muerta": nunca llegó SEND_RECORDING)
--   send_recording_ok    Metadata enviada a OdioIQ con éxito, sesión cerrada
--   send_recording_error Falló el envío de metadata a OdioIQ
--   stream_task_done     La tarea run_both_live terminó sin excepción
--   stream_task_error    La tarea run_both_live terminó con excepción
--                        (⚠️ señal de "recursos/conectividad": WS remoto a
--                        Odio lento, caído, o error de red)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS activity_log (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    call_uuid       VARCHAR(128)   NOT NULL,
    event_type      VARCHAR(30)    NOT NULL,
    state_after     VARCHAR(20)    NULL,
    call_type       VARCHAR(10)    NULL,
    agent_id        VARCHAR(128)   NULL,
    customer_name   VARCHAR(255)   NULL,
    coe_name        VARCHAR(128)   NULL,
    hangup_who      VARCHAR(20)    NULL,
    hangup_cause    SMALLINT       NULL,
    bytes_rx        INT            NULL,
    bytes_tx        INT            NULL,
    duration_ms     INT            NULL,          -- transcurrido desde created_at de la sesión
    detail          VARCHAR(512)   NULL,           -- URL de grabación, mensaje de error, etc.
    host_pid        INT            NULL,
    created_at      DATETIME(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_call_uuid (call_uuid),
    KEY idx_event_type (event_type),
    KEY idx_agent_created (agent_id, created_at),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- activity_snapshots — pulso global del proceso (recursos vs. colas)
-- ----------------------------------------------------------------------------
-- Una fila cada ACTIVITY_SNAPSHOT_INTERVAL segundos (default 15s). Bajo
-- volumen de escritura (~5760 filas/día), InnoDB de sobra.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS activity_snapshots (
    id                      BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    captured_at             DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    sessions_total          INT             NOT NULL,
    sessions_ringing        INT             NOT NULL,
    sessions_answered       INT             NOT NULL,
    sessions_paused         INT             NOT NULL,
    sessions_finished       INT             NOT NULL,  -- finished pero aún NO popped (esperando SEND_RECORDING)
    max_rx_queue            INT             NOT NULL,
    max_tx_queue            INT             NOT NULL,
    avg_rx_queue            DECIMAL(6,2)    NOT NULL,
    avg_tx_queue            DECIMAL(6,2)    NOT NULL,
    active_stream_tasks     INT             NOT NULL,  -- session.stream_task vivos
    pending_merge_tasks     INT             NOT NULL,  -- merges estéreo aún corriendo
    asyncio_task_count      INT             NOT NULL,  -- len(asyncio.all_tasks()) del proceso
    event_loop_lag_ms       DECIMAL(8,2)    NULL,       -- retraso de scheduling del loop
    host_pid                INT             NOT NULL,
    KEY idx_captured_at (captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- Mantenimiento sugerido (no automático): las tablas InnoDB crecen sin
-- límite. Ejemplo de purga manual/cron para conservar 30 días:
--
--   DELETE FROM activity_log       WHERE created_at  < NOW() - INTERVAL 30 DAY;
--   DELETE FROM activity_snapshots WHERE captured_at < NOW() - INTERVAL 30 DAY;
--
-- Si el volumen crece mucho, considera particionar activity_log por RANGE
-- sobre created_at (fuera del alcance de esta primera versión).
-- ----------------------------------------------------------------------------
