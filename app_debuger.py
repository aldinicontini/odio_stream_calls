
import sys
from dotenv import load_dotenv
import os
import logging

load_dotenv()
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

def init_debugger(LOG_FILE="app.log"):
    # Configurar logging
    logging.basicConfig(
        filename=LOG_FILE,
        filemode='a',              # 'a' para append, 'w' para sobrescribir
        level=logging.DEBUG if DEBUG else logging.INFO,
        format='[%(asctime)s] [%(levelname)s] - %(message)s',
        # stream=sys.stdout,
    )

    return logging