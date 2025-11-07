import asyncio
import json
import os
import sys
import socket
import subprocess
from dotenv import load_dotenv
from contextlib import closing

from app_debuger import init_debugger

load_dotenv()

LOCAL_HOST = os.getenv('LOCAL_HOST')
LOCAL_PORT = int(os.getenv('LOCAL_PORT'))
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS', './connections.log')
PING_TIMEOUT = float(os.getenv('PING_TIMEOUT', '1.0'))

LOG_FILE_LOCAL_GATEWAY = os.getenv('LOG_FILE_LOCAL_GATEWAY', './connections.log')
logging = init_debugger(LOG_FILE_LOCAL_GATEWAY)


def is_port_in_use(host, port):
    """Verifica si el puerto está en uso (escuchando alguien)."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        return s.connect_ex((host, port)) == 0


async def ping_local_server(host, port, timeout=PING_TIMEOUT):
    """Intenta abrir una conexión al puerto (como 'ping' TCP)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def get_pid_on_port(port):
    """Obtiene el PID del proceso que está usando el puerto TCP."""
    try:
        # Usar ss si está disponible (más moderno que netstat)
        cmd = f"ss -ltnp | grep :{port}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        output = proc.stdout.strip()
        if output:
            # Extrae el PID entre comillas, ej: users:(("python3",pid=1234,fd=3))
            import re
            match = re.search(r'pid=(\d+)', output)
            if match:
                return int(match.group(1))
        # Fallback a netstat
        cmd = f"netstat -tulpn 2>/dev/null | grep :{port}"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        output = proc.stdout.strip()
        if output:
            import re
            match = re.search(r'/(\d+)/', output)
            if match:
                return int(match.group(1))
    except Exception as e:
        logging.error(f"[ERROR] No se pudo obtener el PID en el puerto {port}: {e}")
    return None


async def ensure_single_instance():
    """Evita instancias duplicadas. Si hay un servidor zombie, lo reemplaza."""
    if is_port_in_use(LOCAL_HOST, LOCAL_PORT):
        logging.warning(f"[WARN] El puerto {LOCAL_PORT} ya está en uso. Verificando si el servidor responde...")

        # Si responde, es una instancia viva → salir
        if await ping_local_server(LOCAL_HOST, LOCAL_PORT, timeout=PING_TIMEOUT):
            logging.info("[INFO] El servidor actual está funcionando correctamente. Esta instancia se cerrará.")
            sys.exit(0)

        # Si no responde → obtener el PID
        logging.warning("[WARN] El puerto está ocupado pero el servidor no responde. Posible proceso atascado.")
        pid = get_pid_on_port(LOCAL_PORT)

        if not pid:
            logging.error(f"[ERROR] No se pudo determinar el PID que ocupa el puerto {LOCAL_PORT}. Abortando.")
            sys.exit(1)

        logging.warning(f"[WARN] Terminando proceso atascado con PID {pid}...")
        try:
            os.kill(pid, 9)
            await asyncio.sleep(1)
        except ProcessLookupError:
            logging.info(f"[INFO] El proceso con PID {pid} ya no existe.")
        except Exception as e:
            logging.error(f"[ERROR] No se pudo matar el proceso {pid}: {e}")
            sys.exit(1)

        # Confirmar que el puerto se liberó
        if is_port_in_use(LOCAL_HOST, LOCAL_PORT):
            logging.error(f"[ERROR] El puerto {LOCAL_PORT} sigue ocupado después de intentar liberar. Abortando.")
            sys.exit(1)

        logging.info("[INFO] Puerto liberado correctamente. Continuando con nuevo servidor.")


# ============================================================
# SERVIDOR LOCAL
# ============================================================

async def handle_local_connection(reader, ws):
    """Recibe datos de un cliente local y los reenvía al WSS."""
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                logging.info("[INFO] Cliente local desconectado")
                break

            try:
                message = json.loads(data.decode())
                await ws.send(json.dumps(message))
            except Exception as e:
                logging.exception(f"[ERROR] Al enviar al WSS {e}")

    except asyncio.CancelledError:
        logging.info("[INFO] Conexión local cancelada")
        pass


async def local_server(ws):
    """Servidor TCP local que recibe conexiones de chunker."""
    await ensure_single_instance()

    async def client_handler(reader, writer):
        try:
            await handle_local_connection(reader, ws)
        except Exception:
            logging.exception("[ERROR] Conexión local falló")
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(client_handler, LOCAL_HOST, LOCAL_PORT)
    logging.info(f"[INFO] Proxy escuchando en {LOCAL_HOST}:{LOCAL_PORT}")

    async with server:
        await server.serve_forever()
