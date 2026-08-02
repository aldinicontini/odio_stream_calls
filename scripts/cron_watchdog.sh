#!/usr/bin/env bash

# cron_watchdog.sh — Script para ejecutar cada 2 horas vía cron.
# Verifica que el proceso principal (main.py) y sus puertos (9019, 9020) estén en línea.
# Si el servidor no responde o está caído, lo reinicia automáticamente.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$PROJECT_ROOT/logs/cron_watchdog.log"
PYTHON_BIN="$PROJECT_ROOT/venv/bin/python3"

# Crear directorio de logs si no existe
mkdir -p "$PROJECT_ROOT/logs"

timestamp() {
    date "+%Y-%m-%d %H:%M:%S"
}

echo "[$(timestamp)] [INFO] Running cron watchdog check..." >> "$LOG_FILE"

# Si no existe venv local, intentar usar python3 del sistema
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(which python3)
fi

# Script inline para verificar puertos 9019 y 9020 usando python
HEALTH_CHECK=$("$PYTHON_BIN" -c "
import asyncio, os, sys
from utils.socket_utils import ping_local_server

async def check():
    audio_port = int(os.getenv('AUDIO_PORT', '9019'))
    control_port = int(os.getenv('CONTROL_PORT', '9020'))
    host = os.getenv('LOCAL_HOST', '127.0.0.1')
    
    audio_ok = await ping_local_server(host, audio_port, timeout=2.0)
    control_ok = await ping_local_server(host, control_port, timeout=2.0)
    
    if audio_ok and control_ok:
        sys.exit(0)
    else:
        sys.exit(1)

asyncio.run(check())
" 2>&1)

STATUS=$?

if [ $STATUS -eq 0 ]; then
    echo "[$(timestamp)] [OK] All sockets (AUDIO & CONTROL) are responsive and online." >> "$LOG_FILE"
    exit 0
else
    echo "[$(timestamp)] [WARN] Health check failed! Sockets non-responsive or service down. Starting recovery..." >> "$LOG_FILE"
    
    # Matar cualquier proceso colgado en los puertos
    "$PYTHON_BIN" -c "
import asyncio, os
from utils.socket_utils import ensure_single_instance

async def clean():
    audio_port = int(os.getenv('AUDIO_PORT', '9019'))
    control_port = int(os.getenv('CONTROL_PORT', '9020'))
    host = os.getenv('LOCAL_HOST', '127.0.0.1')
    await ensure_single_instance(host, audio_port)
    await ensure_single_instance(host, control_port)

asyncio.run(clean())
" >> "$LOG_FILE" 2>&1

    # Iniciar servidor main.py en segundo plano
    echo "[$(timestamp)] [INFO] Launching main.py in background..." >> "$LOG_FILE"
    cd "$PROJECT_ROOT"
    nohup "$PYTHON_BIN" main.py >> "$PROJECT_ROOT/logs/main_server.log" 2>&1 &
    
    sleep 2

    # Verificar que las conexiones se restablecieron
    "$PYTHON_BIN" -c "
import asyncio, os, sys
from utils.socket_utils import ping_local_server

async def check():
    audio_port = int(os.getenv('AUDIO_PORT', '9019'))
    control_port = int(os.getenv('CONTROL_PORT', '9020'))
    host = os.getenv('LOCAL_HOST', '127.0.0.1')
    
    audio_ok = await ping_local_server(host, audio_port, timeout=2.0)
    control_ok = await ping_local_server(host, control_port, timeout=2.0)
    
    if audio_ok and control_ok:
        sys.exit(0)
    else:
        sys.exit(1)

asyncio.run(check())
" 2>&1

    POST_STATUS=$?
    if [ $POST_STATUS -eq 0 ]; then
        echo "[$(timestamp)] [SUCCESS] main.py process restarted and sockets are online!" >> "$LOG_FILE"
    else
        echo "[$(timestamp)] [ERROR] Failed to verify socket health after restart!" >> "$LOG_FILE"
    fi
fi
