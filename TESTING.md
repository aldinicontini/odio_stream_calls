# TESTING.md — Guía de activación y pruebas del servidor event-driven

## 1. Preparación del entorno

```bash
cd /usr/local/bin/odio_stream_calls
source venv/bin/activate
pip install -r requirements.txt
```

Edita `.env` y **cambia** el token de control antes de exponerlo a Internet:

```
CONTROL_AUTH_TOKEN=pon_aqui_un_token_secreto_largo
```

---

## 2. Arrancar el servidor

```bash
python main.py
```

Deberías ver en consola:

```
[AUDIO] StreamSocket listening on 0.0.0.0:9019
[CONTROL] WebSocket listening on ws://0.0.0.0:9020
```

Y en `logs/connection.log`:

```
[...] [INFO] - StreamSocket server listening on ('0.0.0.0', 9019)
[...] [INFO] - [CONTROL] WebSocket control server listening on 0.0.0.0:9020
```

> **Nota:** Los logs ya no se mezclan en un solo archivo raíz.
> Todos los archivos de log quedan en la carpeta `logs/` del proyecto.

---

## 3. Qué conecta Asterisk (puerto 9019)

Asterisk conecta automáticamente al recibir una llamada configurada con
`AudioSocket`. No se necesita hacer nada manual en este paso.

Cuando Asterisk conecta, el servidor:
1. Recibe el paquete `UUID` → crea una `CallSession` con `NullSink`
2. Recibe frames `AUDIO_RX` / `AUDIO_TX` → los descarta (NullSink)
3. Espera el evento `ANSWER` del agente antes de enviar audio a odio

---

## 4. Lo que envía el agente desde el navegador (puerto 9020)

### 4.1 Establecer la conexión WebSocket

El cliente JavaScript abre un WebSocket al puerto 9020:

```javascript
const ws = new WebSocket("ws://<IP_DEL_SERVIDOR>:9020");
```

Para conexión segura en producción (detrás de nginx/proxy):

```javascript
const ws = new WebSocket("wss://<DOMINIO>/control");
```

---

### 4.2 Mensaje ANSWER — el agente contesta la llamada

**Cuándo enviarlo:** Inmediatamente después de que el agente presione
"Contestar" en la interfaz.

**Dirección:** Cliente → Servidor

```json
{
    "type": "ANSWER",
    "token": "pon_aqui_un_token_secreto_largo",
    "callid": "1762560249.55706",
    "agent": "1001",
    "customer_information": {
        "tenantId": "75612601",
        "coeName": "CSCVIP",
        "agentName": "Juan Pérez",
        "agentId": "juan.perez",
        "customerPhone": "3169286933",
        "customerName": "María García",
        "callTime": "2026-08-02 14:30:00",
        "callType": "outbound"
    }
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `type` | string | Siempre `"ANSWER"` |
| `token` | string | Valor de `CONTROL_AUTH_TOKEN` en `.env` |
| `callid` | string | UUID de la llamada (el mismo que Asterisk asignó) |
| `agent` | string | ID del agente que contestó |
| `customer_information` | object | Datos de la llamada que se envían al inicio del stream en odio |

**Respuesta esperada del servidor:**

```json
{"type": "ACK", "callid": "1762560249.55706"}
```

Después de recibir el ACK, el servidor ya está transmitiendo audio en
tiempo real hacia odio. **El primer frame de audio que llegue de Asterisk
después del ANSWER irá directamente al WebSocket hacia odio.**

---

### 4.3 Mensaje PING — keepalive (opcional)

Para mantener la conexión abierta si el navegador lo necesita:

```json
{"type": "PING", "token": "pon_aqui_un_token_secreto_largo"}
```

**Respuesta:**

```json
{"type": "PONG"}
```

---

### 4.4 Mensaje HANGUP — colgar desde el agente (opcional)

Si el agente cuelga desde la interfaz antes de que Asterisk envíe el hangup:

```json
{
    "type": "HANGUP",
    "token": "pon_aqui_un_token_secreto_largo",
    "callid": "1762560249.55706"
}
```

**Respuesta:**

```json
{"type": "ACK", "callid": "1762560249.55706"}
```

---

### 4.5 Respuesta de error

Cuando algo sale mal el servidor responde con un objeto `ERROR`:

```json
{
    "type": "ERROR",
    "code": "NOT_FOUND",
    "message": "Session '1762560249.55706' not found or already closed"
}
```

| Código | Causa |
|---|---|
| `INVALID_JSON` | El mensaje no es JSON válido |
| `INVALID_FORMAT` | El JSON no es un objeto |
| `UNAUTHORIZED` | Token inválido o ausente |
| `MISSING_FIELDS` | Faltan campos obligatorios |
| `INVALID_FIELDS` | Un campo tiene el tipo incorrecto |
| `NOT_FOUND` | La sesión no existe (la llamada no llegó aún o ya terminó) |
| `ALREADY_ANSWERED` | La sesión ya fue contestada por otro agente |
| `HANGUP` | La llamada ya terminó |
| `UNKNOWN_TYPE` | Tipo de mensaje no reconocido |

---

## 5. Ejemplo JavaScript completo (navegador del agente)

```javascript
const SERVER_WS = "ws://192.168.1.100:9020";
const TOKEN     = "pon_aqui_un_token_secreto_largo";

let ws;

function conectarControl() {
    ws = new WebSocket(SERVER_WS);

    ws.onopen = () => {
        console.log("[CONTROL] Conectado al servidor de control");
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        console.log("[CONTROL] Respuesta:", msg);

        if (msg.type === "ACK") {
            console.log(`✅ Llamada ${msg.callid} en streaming hacia odio`);
        } else if (msg.type === "ERROR") {
            console.error(`❌ Error ${msg.code}: ${msg.message}`);
        }
    };

    ws.onclose = () => console.warn("[CONTROL] Conexión cerrada");
    ws.onerror = (e) => console.error("[CONTROL] Error WebSocket:", e);
}

function contestarLlamada(callid, agentId, customerInfo) {
    ws.send(JSON.stringify({
        type: "ANSWER",
        token: TOKEN,
        callid: callid,
        agent: agentId,
        customer_information: customerInfo
    }));
}

function colgarLlamada(callid) {
    ws.send(JSON.stringify({
        type: "HANGUP",
        token: TOKEN,
        callid: callid
    }));
}

// Inicializar al cargar la página del agente
conectarControl();
```

---

## 6. ¿Dónde ver los logs?

Todos los logs quedan en `logs/` dentro del directorio del proyecto:

```
logs/
└── connection.log    ← logs del servidor de audio y control
```

Para seguirlos en tiempo real:

```bash
tail -f /usr/local/bin/odio_stream_calls/logs/connection.log
```

---

## 7. Qué NO cambia (retrocompatibilidad)

- `launch.sh` sigue funcionando para el flujo legacy (WAV → odio)
- `python stream_socket.py <archivo> --test` sigue funcionando sin cambios
- `python audio_socket_server.py` arranca el servidor de audio standalone

---

## 8. Flujo completo esperado en los logs

```
[INFO] New StreamSocket connection from ('34.45.236.70', 52341)
[INFO] Call UUID: 1762560249.55706 — session created (NullSink active)
  ... frames RX/TX descartados silenciosamente por NullSink ...
[INFO] [CONTROL] Agent connected from ('203.0.113.42', 61234)
[INFO] [CONTROL] ANSWER callid=1762560249.55706 agent=1001
[INFO] 1762560249.55706 - [LIVE] Starting 'outbound' live stream. Agent: 1001
[INFO] 1762560249.55706 - [LIVE] WebSocket connection established.
[INFO] 1762560249.55706 - [outbound] Starting live stream from queue
[INFO] 1762560249.55706 - [inbound] Starting live stream from queue
  ... frames transmitidos a odio en tiempo real ...
[INFO] Hangup received for 1762560249.55706 — colgó: TX — cause=16
[INFO] 1762560249.55706 - [outbound] Hangup sentinel received, ending live stream.
[INFO] 1762560249.55706 - [inbound] Hangup sentinel received, ending live stream.
[INFO] 1762560249.55706 - [LIVE] WebSocket connection closed correctly.
[INFO] Finished call 1762560249.55706 — rx=128000B tx=64000B
```
