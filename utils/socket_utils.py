import asyncio
import os
import re
import socket
import subprocess
import sys
from contextlib import closing
from utils.app_debugger import init_debugger

default_logger = init_debugger("connections.log")


def is_port_in_use(host: str, port: int) -> bool:
    """Verifica si el puerto está en uso (socket escuchando)."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        return s.connect_ex((host, port)) == 0


async def ping_local_server(host: str, port: int, timeout: float = 1.0, is_ws: bool = False) -> bool:
    """
    Intenta abrir una conexión al puerto (ping TCP / WebSocket).

    Para servidores WebSocket (is_ws=True), realiza un handshake de WebSocket
    para evitar errores de parsing HTTP en los logs del servidor.
    """
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host

    if is_ws:
        try:
            import websockets
            url = f"ws://{connect_host}:{port}"
            async with websockets.connect(url, open_timeout=timeout, close_timeout=timeout):
                return True
        except Exception:
            # Si el puerto responde (incluso con error de handshake de auth/HTTP), el puerto está activo
            return is_port_in_use(connect_host, port)
    else:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(connect_host, port), timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False


def get_pid_on_port(port: int, logger=None) -> int | None:
    """
    Obtiene el PID del proceso que ESCUCHA en el puerto TCP (usando ss o lsof).

    Solo sockets en LISTEN: un 'lsof -ti :<port>' sin filtro también devuelve
    a los clientes conectados al puerto (Asterisk vía StreamSocket, Apache...),
    y quien llama a esta función mata ese PID con SIGKILL.
    """
    log = logger or default_logger
    try:
        # 1. Intentar con ss (filtro exacto por puerto, sin encabezado)
        proc = subprocess.run(
            ["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True
        )
        match = re.search(r'pid=(\d+)', proc.stdout)
        if match:
            return int(match.group(1))

        # 2. Intentar con lsof
        proc = subprocess.run(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
        )
        pids = proc.stdout.split()
        if pids:
            return int(pids[0])
    except Exception as e:
        log.error(f"[ERROR] No se pudo obtener el PID en el puerto {port}: {e}")
    return None


async def ensure_single_instance(host: str, port: int, ping_timeout: float = 1.0, is_ws: bool = False, logger=None) -> bool:
    """
    Evita instancias duplicadas o puertos bloqueados por procesos zombies.
    
    1. Si el puerto está en uso y el servidor responde correctamente:
       Retorna True (puerto activo y saludable).
    2. Si el puerto está en uso pero NO responde (zombie):
       Obtiene el PID y mata el proceso (`kill -9`) para liberar el puerto.
    3. Retorna True si el puerto queda disponible para iniciar el servidor.
    """
    log = logger or default_logger

    if is_port_in_use(host, port):
        log.warning(f"[WARN] El puerto {port} ({host}) ya está en uso. Verificando capacidad de respuesta...")

        # Si responde a ping, el servidor está funcionando correctamente
        if await ping_local_server(host, port, timeout=ping_timeout, is_ws=is_ws):
            log.info(f"[INFO] El servidor en {host}:{port} responde correctamente.")
            return True

        # Si no responde → proceso atascado/zombie
        log.warning(f"[WARN] El puerto {port} está ocupado pero no responde. Posible proceso zombie.")
        pid = get_pid_on_port(port, logger=log)

        if not pid:
            log.error(f"[ERROR] No se pudo determinar el PID que ocupa el puerto {port}.")
            return False

        log.warning(f"[WARN] Terminando proceso atascado con PID {pid} en puerto {port}...")
        try:
            os.kill(pid, 9)
            await asyncio.sleep(1)
        except ProcessLookupError:
            log.info(f"[INFO] El proceso con PID {pid} ya no existe.")
        except Exception as e:
            log.error(f"[ERROR] No se pudo matar el proceso {pid}: {e}")
            return False

        # Confirmar que el puerto se liberó
        if is_port_in_use(host, port):
            log.error(f"[ERROR] El puerto {port} sigue ocupado tras intentar liberar.")
            return False

        log.info(f"[INFO] Puerto {port} liberado correctamente.")

    return True
