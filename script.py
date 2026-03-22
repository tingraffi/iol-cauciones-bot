import requests
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

INTERES_TASAS = [40, 50, 60, 70, 80, 90, 100]
ultimo_umbral_avisado = 0
last_update_id = 0 # Para rastrear mensajes nuevos de Telegram
ARG_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ultima_tasa_valida = None


def log(msg):
    ts = datetime.now(ARG_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def extraer_panel_cauciones(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("cotizaciones", "data", "items", "resultado", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []

def obtener_token():
    url = "https://api.invertironline.com/token"
    payload = {'username': os.getenv('IOL_USERNAME'), 'password': os.getenv('IOL_PASSWORD'), 'grant_type': 'password'}
    if not payload['username'] or not payload['password']:
        return None, "Faltan IOL_USERNAME o IOL_PASSWORD en variables de entorno"

    try:
        r = requests.post(url, data=payload, timeout=10)
    except requests.RequestException as exc:
        return None, f"Error de red al pedir token: {exc}"

    if r.status_code != 200:
        detalle = ""
        try:
            detalle = r.json()
        except ValueError:
            detalle = r.text[:300]
        return None, f"Token IOL HTTP {r.status_code}: {detalle}"

    try:
        token = r.json().get('access_token')
    except ValueError:
        return None, "Respuesta de token no es JSON"

    if not token:
        return None, "No vino access_token en la respuesta de IOL"

    return token, None

def consultar_tasa_dinamica(token):
    urls = [
        "https://api.invertironline.com/api/v2/Cotizaciones/Cauciones/PESOS",
        "https://api.invertironline.com/api/v2/Cotizaciones/Cauciones/PESOS/1",
    ]
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'User-Agent': 'iol-cauciones-bot/1.0'
    }

    if not token:
        return {"ok": False, "motivo": "Sin token de autenticacion"}

    ultimo_error = None
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
        except requests.RequestException as exc:
            ultimo_error = {"ok": False, "motivo": f"Error de red consultando cauciones: {exc}"}
            continue

        if r.status_code >= 500:
            # IOL suele responder 500 con HTML fuera de horario o por saturacion.
            ultimo_error = {
                "ok": False,
                "motivo": "IOL temporalmente no disponible (HTTP 5xx)",
                "detalle": f"Endpoint: {url}"
            }
            continue

        if r.status_code != 200:
            detalle = ""
            content_type = r.headers.get('Content-Type', '').lower()
            if 'application/json' in content_type:
                try:
                    detalle = str(r.json())[:300]
                except ValueError:
                    detalle = r.text[:120]
            else:
                detalle = f"Respuesta no JSON ({content_type or 'desconocido'})"

            ultimo_error = {
                "ok": False,
                "motivo": f"Cauciones HTTP {r.status_code}",
                "detalle": detalle
            }
            continue

        try:
            payload = r.json()
        except ValueError:
            ultimo_error = {"ok": False, "motivo": "Respuesta de cauciones no es JSON"}
            continue

        panel = extraer_panel_cauciones(payload)
        mejor_tasa = 0
        mejor_plazo = "N/A"

        for c in panel:
            puntas = c.get('puntas') or []
            if not puntas:
                continue

            for punta in puntas:
                tasa_actual = punta.get('tasa', 0)
                if tasa_actual > mejor_tasa:
                    mejor_tasa = tasa_actual
                    mejor_plazo = c.get('plazo', 'N/A')

        if mejor_tasa > 0:
            return {"ok": True, "tasa": mejor_tasa, "plazo": mejor_plazo}

        ultimo_error = {
            "ok": False,
            "motivo": "Sin puntas con tasa",
            "detalle": "Mercado posiblemente cerrado o sin liquidez"
        }

    return ultimo_error or {
        "ok": False,
        "motivo": "No se pudo obtener tasa de cauciones",
        "detalle": "Sin respuesta util de IOL"
    }

def enviar_telegram(mensaje):
    token_tg = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    url = f"https://api.telegram.org/bot{token_tg}/sendMessage"
    requests.post(url, json={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'Markdown'}, timeout=10)


def obtener_mejor_tasa():
    global ultima_tasa_valida

    token, err_token = obtener_token()
    if err_token:
        return None, None, f"No se pudo autenticar en IOL: {err_token}"

    resultado = consultar_tasa_dinamica(token)
    if resultado.get("ok"):
        tasa = resultado.get("tasa")
        plazo = resultado.get("plazo")
        ultima_tasa_valida = {
            "tasa": tasa,
            "plazo": plazo,
            "timestamp": datetime.now(ARG_TZ).strftime("%Y-%m-%d %H:%M:%S")
        }
        return tasa, plazo, None

    motivo = resultado.get("motivo", "Error desconocido")
    detalle = resultado.get("detalle")
    if detalle:
        return None, None, f"{motivo}. {detalle}"
    return None, None, motivo

def revisar_comandos():
    global last_update_id
    token_tg = os.getenv('TELEGRAM_TOKEN')
    url = f"https://api.telegram.org/bot{token_tg}/getUpdates"
    try:
        r = requests.get(url, params={'offset': last_update_id + 1, 'timeout': 1}, timeout=10)
        updates = r.json().get('result', [])
        for update in updates:
            last_update_id = update['update_id']
            mensaje_recibido = update.get('message', {}).get('text', '').strip().lower()
            
            if mensaje_recibido in ('/tasa', 'tasa'):
                tasa, plazo, error = obtener_mejor_tasa()
                if tasa:
                    enviar_telegram(f"📊 La mejor tasa actual es: *{tasa}%* (Plazo: {plazo} días)")
                else:
                    if ultima_tasa_valida:
                        enviar_telegram(
                            "📊 Mercado sin tasa en vivo ahora. "
                            f"Ultima valida: *{ultima_tasa_valida['tasa']}%* "
                            f"(Plazo: {ultima_tasa_valida['plazo']} dias, "
                            f"capturada: {ultima_tasa_valida['timestamp']} AR). "
                            f"Motivo: *{error or 'Mercado cerrado o sin puntas'}*"
                        )
                    else:
                        enviar_telegram(f"📊 Estado: *{error or 'Mercado cerrado o sin puntas'}*")
                    
            elif mensaje_recibido in ('/status', 'status'):
                ahora = datetime.now(ARG_TZ)
                en_horario = ahora.weekday() <= 4 and 11 <= ahora.hour < 17
                enviar_telegram(
                    f"🤖 Bot *Online* | Hora AR: {ahora.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"Horario de mercado: {'SI' if en_horario else 'NO'}"
                )
    except Exception as exc:
        log(f"Error revisando comandos de Telegram: {exc}")

# --- LOOP PRINCIPAL ---
log("Bot iniciado...")
while True:
    ahora = datetime.now(ARG_TZ)
    
    # Siempre revisamos si hay comandos (aunque sea de noche)
    revisar_comandos()

    if ahora.weekday() <= 4 and 11 <= ahora.hour < 17:
        tasa, plazo, error = obtener_mejor_tasa()
        if tasa:
            for nivel in reversed(INTERES_TASAS):
                if tasa >= nivel:
                    if ultimo_umbral_avisado != nivel:
                        enviar_telegram(f"💰 *ALERTA*: Tasa en *{tasa}%* a {plazo} días (Nivel {nivel}%)")
                        ultimo_umbral_avisado = nivel
                    break
            if tasa < (ultimo_umbral_avisado - 5):
                ultimo_umbral_avisado = 0
        else:
            log(f"Sin tasa para alertas: {error}")
        time.sleep(60) # Revisamos tasa cada minuto, pero comandos mas seguido
    else:
        time.sleep(10) # Fuera de hora, revisamos comandos cada 10 seg.