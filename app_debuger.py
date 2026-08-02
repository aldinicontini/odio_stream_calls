
import sys
from dotenv import load_dotenv
import os
import logging

load_dotenv()
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

def init_debugger(LOG_FILE="app.log"):
    # Ensure the directory of the log file exists, or fallback to current directory
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            # Fallback if we don't have write permissions to the directory (e.g., /usr/local/bin)
            LOG_FILE = os.path.basename(LOG_FILE)

    # Configurar logging
    logging.basicConfig(
        filename=LOG_FILE,
        filemode='a',              # 'a' para append, 'w' para sobrescribir
        level=logging.DEBUG if DEBUG else logging.INFO,
        format='[%(asctime)s] [%(levelname)s] - %(message)s',
        # stream=sys.stdout,
    )

    return logging