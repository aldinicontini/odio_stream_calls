
import os
import logging
from dotenv import load_dotenv

load_dotenv()
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

# Directorio base para logs cuando se usan rutas relativas
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")


def _try_mkdir(path: str) -> bool:
    """Intenta crear un directorio. Retorna True si existe o fue creado."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except (PermissionError, OSError):
        return False


def _resolve_log_path(log_file: str) -> str:
    """
    Resuelve la ruta final del log file siguiendo esta estrategia:

    Ruta absoluta (ej: /usr/local/bin/.../connection.log)
    -------------------------------------------------------
    1. Intenta usar  <mismo-directorio>/logs/<nombre>.log
    2. Si no tiene permisos, usa la ruta original tal cual

    Ruta relativa o solo nombre
    ----------------------------
    1. Siempre en  <proyecto>/logs/<nombre>.log
    """
    if os.path.isabs(log_file):
        parent_dir = os.path.dirname(log_file)
        logs_subdir = os.path.join(parent_dir, "logs")
        if _try_mkdir(logs_subdir):
            return os.path.join(logs_subdir, os.path.basename(log_file))
        # Fallback: usar la ruta absoluta original (ya funcionaba antes)
        return log_file
    else:
        _try_mkdir(_LOGS_DIR)
        return os.path.join(_LOGS_DIR, os.path.basename(log_file))


def init_debugger(log_file: str = "app.log") -> logging.Logger:
    """
    Crea (o reutiliza) un Logger nombrado que escribe en logs/<nombre>.

    Retorna un objeto Logger — no el módulo logging — de modo que cada módulo
    tenga su propio destino sin mezclar mensajes entre archivos.

    Estrategia de fallback:
      1. Intenta escribir en  logs/<nombre>.log  (junto al log original)
      2. Si hay PermissionError, escribe en la ruta original
      3. Como último recurso, escribe en stderr para no silenciar errores

    Los handlers no se duplican aunque el módulo se reimporte.
    """
    resolved_path = _resolve_log_path(log_file)
    logger_name = os.path.splitext(os.path.basename(resolved_path))[0]

    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        handler: logging.Handler
        try:
            handler = logging.FileHandler(resolved_path, mode='a', encoding='utf-8')
        except (PermissionError, OSError):
            # Segundo intento: ruta original absoluta
            try:
                handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            except (PermissionError, OSError):
                # Último recurso: stderr
                handler = logging.StreamHandler()

        handler.setFormatter(
            logging.Formatter('[%(asctime)s] [%(levelname)s] - %(message)s')
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        logger.propagate = False  # no propagar al root logger

    return logger