
import os
import logging
from dotenv import load_dotenv

load_dotenv()
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

# ---------------------------------------------------------------------------
# Directorio base para todos los archivos de log del proyecto.
# Se crea automáticamente si no existe.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)


def _resolve_log_path(log_file: str) -> str:
    """
    Garantiza que el log termine siempre dentro de logs/.

    - Ruta absoluta (ej: /usr/local/bin/.../connection.log):
      se extrae el basename y se redirige a logs/connection.log
    - Ruta relativa o solo nombre:
      se coloca directamente en logs/
    """
    return os.path.join(_LOGS_DIR, os.path.basename(log_file))


def init_debugger(log_file: str = "app.log") -> logging.Logger:
    """
    Crea (o reutiliza) un Logger nombrado que escribe exclusivamente en logs/<nombre>.

    Retorna un objeto Logger — no el módulo logging — de modo que cada módulo
    tenga su propio destino sin mezclar mensajes entre archivos.

    Los handlers no se duplican aunque el módulo se reimporte.
    El logger no propaga al root logger para evitar interferencias con
    librerías externas (websockets, asyncio, etc.).

    Uso idéntico al código anterior:
        logging = init_debugger(LOG_FILE_CONNECTIONS)
        logging.info("mensaje")
        logging.error("error")
    """
    resolved_path = _resolve_log_path(log_file)
    logger_name = os.path.splitext(os.path.basename(resolved_path))[0]

    logger = logging.getLogger(logger_name)

    # Evitar añadir handlers duplicados si el módulo se reimporta
    if not logger.handlers:
        handler = logging.FileHandler(resolved_path, mode='a', encoding='utf-8')
        handler.setFormatter(
            logging.Formatter('[%(asctime)s] [%(levelname)s] - %(message)s')
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        logger.propagate = False  # no propagar al root logger

    return logger