import os
from utils.app_debugger import init_debugger
LOG_FILE = os.getenv("LOG_FILE_CONNECTIONS", "audiosocket.log")
logger = init_debugger(LOG_FILE)

_AGENT_ALLOWED_RAW = os.getenv("AGENT_ALLOWED", "")
AGENT_ALLOWED = {
    a.strip().lower()
    for a in _AGENT_ALLOWED_RAW.split(",")
    if a.strip()
}

if AGENT_ALLOWED:
    logger.info(f"[CONTROL] AGENT_ALLOWED filter active: {sorted(AGENT_ALLOWED)}")
else:
    logger.info("[CONTROL] AGENT_ALLOWED not configured — all agents permitted")


def _is_agent_allowed(agent: str) -> bool:
    """
    Retorna True si no hay whitelist configurada (AGENT_ALLOWED vacío),
    o si el agente está presente en ella (comparación case-insensitive).
    Este chequeo aplica únicamente al live socket con Odio (ANSWER).
    """
    if not AGENT_ALLOWED:
        return True
    return agent.strip().lower() in AGENT_ALLOWED

def _is_tenant_allowed(tenant: str) -> bool:
    """
    Retorna True si no hay whitelist configurada (AGENT_ALLOWED vacío),
    o si el tenant está presente en ella (comparación case-insensitive).
    Este chequeo aplica únicamente al live socket con Odio (ANSWER).
    """
    if not AGENT_ALLOWED:
        return True
    return tenant.strip().lower() in AGENT_ALLOWED