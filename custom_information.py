import json
import os
from app_debuger import init_debugger
LOG_FILE_CONNECTIONS = os.getenv('LOG_FILE_CONNECTIONS')
logging = init_debugger(LOG_FILE_CONNECTIONS)

### this shoul be replaced for SQLlite


#total de informacion previamente cargada a customer_information
# {
#     'coeName': 'CSCVIP',
#     'agentName': 'Richel Carreon',
#     'agentId': 'Richel.Carreon',
#     'customerName': '',
#     'customerPhoneNumber': '3169286933',
#     'lead_id': '7948775',
#     'caller_code': '020940140007948775',
#     'recording_name': 'company-1780411215.39155',
#     'uniqueid2': '0',
#     'uuid': '61cf8a6e-2e37-4624-8457-562a6cd36e52',
#     'callTime': '2026-06-02 09: 40: 15',
#     'event_date': '2026-06-02'
# }

def get_customer_information(filename):
    base_uniqueid = filename.replace("-in.wav", "").replace("-out.wav", "")
    customer_information = None
    log_file = "/var/log/oddio_customer_information.log"

    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith(base_uniqueid + " => "):
                    try:
                        # Extraer la parte JSON después del '=>'
                        json_part = line.split("=>", 1)[1].strip()
                        customer_information = json.loads(json_part)
                    except json.JSONDecodeError as e:
                        logging.error(f"Error decodificando JSON para {base_uniqueid}: {e}")
                    break  # ya lo encontramos, no seguimos leyendo
    except FileNotFoundError:
        logging.warning(f"Archivo de información de cliente no encontrado: {log_file}")
    except Exception as e:
        logging.error(f"Error al obtener información del cliente: {e}")

    return customer_information

