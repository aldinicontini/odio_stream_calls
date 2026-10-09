import os
import json
import asyncio
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

ODIO_METADATA_URL   = os.getenv("ODIO_METADATA_URL", "https://app.odioiq.com/odio/api/metadata")
ODIO_METADATA_TOKEN = os.getenv("ODIO_METADATA_TOKEN", "")
ODIO_HTTP_TIMEOUT   = float(os.getenv("ODIO_HTTP_TIMEOUT", "10"))

# Pool propio: si OdioIQ responde lento, sus hilos no compiten con el pool por
# defecto donde corren los merges de las grabaciones.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="odio-metadata")


class OdioMetadataError(Exception):
    pass


def _post_sync(payload):
    if not ODIO_METADATA_TOKEN:
        raise OdioMetadataError("ODIO_METADATA_TOKEN no configurado")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ODIO_METADATA_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {ODIO_METADATA_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=ODIO_HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, (json.loads(raw) if raw else {})
    except urllib.error.URLError as e:
        raise OdioMetadataError(f"No se pudo conectar a OdioIQ: {e}") from e


async def send_recording_metadata(payload):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _post_sync, payload)
