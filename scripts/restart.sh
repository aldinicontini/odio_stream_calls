#!/usr/bin/env bash

# restart.sh — Script para reiniciar el servidor principal (main.py) y aplicar cambios de código.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$PROJECT_ROOT/venv/bin/python3"

cd "$PROJECT_ROOT" || exit 1
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(which python3)
fi

echo "============================================================"
echo "[INFO] Reiniciando servicio event-driven (main.py)..."
echo "============================================================"

# 1. Matar proceso main.py o cualquier proceso que ocupe los puertos 9019 y 9020
"$PYTHON_BIN" -c "
import os, signal, subprocess

# Matar procesos main.py
try:
    cmd = 'pgrep -f main.py'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    my_pid = os.getpid()
    for line in res.stdout.strip().splitlines():
        if line.strip():
            pid = int(line.strip())
            if pid != my_pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                    print(f'[OK] Terminado proceso main.py (PID {pid})')
                except Exception:
                    pass
except Exception as e:
    print(f'[WARN] Error al detener main.py: {e}')

# Matar cualquier proceso en puertos 9019 y 9020
from utils.socket_utils import get_pid_on_port
for port in [9019, 9020]:
    pid = get_pid_on_port(port)
    if pid and pid != os.getpid():
        try:
            os.kill(pid, signal.SIGKILL)
            print(f'[OK] Liberado puerto {port} (PID {pid})')
        except Exception:
            pass
"

sleep 1

# 2. Iniciar servidor main.py en segundo plano
echo "[INFO] Lanzando main.py en segundo plano..."
nohup "$PYTHON_BIN" main.py >> "$PROJECT_ROOT/logs/main_server.log" 2>&1 &

sleep 2

# 3. Verificar estado de salud de los servicios
"$PYTHON_BIN" -c "
import asyncio, os, sys
from utils.socket_utils import ping_local_server

async def check():
    audio_port = int(os.getenv('AUDIO_PORT', '9019'))
    control_port = int(os.getenv('CONTROL_PORT', '9020'))
    host = os.getenv('LOCAL_HOST', '127.0.0.1')
    
    audio_ok = await ping_local_server(host, audio_port, timeout=2.0, is_ws=False)
    control_ok = await ping_local_server(host, control_port, timeout=2.0, is_ws=True)
    
    if audio_ok and control_ok:
        print('[SUCCESS] ✅ Servidor reiniciado exitosamente.')
        print(f'  - Audio TCP Server:   {host}:{audio_port} [ONLINE]')
        print(f'  - Control WS Gateway: {host}:{control_port} [ONLINE]')
        sys.exit(0)
    else:
        print('[ERROR] ⚠️ Fallo al verificar los servicios tras el reinicio.')
        sys.exit(1)

asyncio.run(check())
"
