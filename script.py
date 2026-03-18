import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

INTERES_TASAS = [40, 50, 60, 70, 80, 90, 100]
ultimo_umbral_avisado = 0
last_update_id = 0 # Para rastrear mensajes nuevos de Telegram

def obtener_token():
    url = "https://api.invertironline.com/token"
    payload = {'username': os.getenv('IOL_USERNAME'), 'password': os.getenv('IOL_PASSWORD'), 'grant_type': 'password'}
    try:
        r = requests.post(url, data=payload)
        return r.json().get('access_token')
    except: return None

# --- NUEVA FUNCIÓN DINÁMICA ---
def consultar_tasa_dinamica(token):
    # Ya no pedimos "/1", pedimos todo el panel de PESOS
    url = "https://api.invertironline.com/api/v2/Cotizaciones/Cauciones/PESOS"
    headers = {'Authorization': f'Bearer {token}'}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            panel = r.json()
            mejor_tasa = 0
            mejor_plazo = ""
            
            # Recorremos todas las cauciones buscando la tasa más alta
            for c in panel:
                if c.get('puntas'):
                    tasa_actual = c['puntas'][0].get('tasa', 0)
                    if tasa_actual > mejor_tasa:
                        mejor_tasa = tasa_actual
                        mejor_plazo = c.get('plazo', 'N/A')
            
            # Si encontramos una tasa válida, devolvemos la tasa y los días
            if mejor_tasa > 0:
                return mejor_tasa, mejor_plazo
        return None, None
    except: return None, None

def enviar_telegram(mensaje):
    token_tg = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    url = f"https://api.telegram.org/bot{token_tg}/sendMessage"
    requests.post(url, json={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'Markdown'})

def revisar_comandos():
    global last_update_id
    token_tg = os.getenv('TELEGRAM_TOKEN')
    url = f"https://api.telegram.org/bot{token_tg}/getUpdates"
    try:
        r = requests.get(url, params={'offset': last_update_id + 1, 'timeout': 1})
        updates = r.json().get('result', [])
        for update in updates:
            last_update_id = update['update_id']
            mensaje_recibido = update.get('message', {}).get('text', '').lower()
            
            if mensaje_recibido == '/tasa':
                tasa, plazo = consultar_tasa_dinamica(obtener_token()) # <- Actualizado acá
                if tasa:
                    enviar_telegram(f"📊 La mejor tasa actual es: *{tasa}%* (Plazo: {plazo} días)")
                else:
                    enviar_telegram("📊 Estado: *Mercado cerrado o sin puntas*")
                    
            elif mensaje_recibido == '/status':
                enviar_telegram("🤖 El bot está *Online* y monitoreando en la Raspberry Pi.")
    except: pass

# --- LOOP PRINCIPAL ---
print("🚀 Bot iniciado...")
while True:
    ahora = datetime.now()
    
    # Siempre revisamos si hay comandos (aunque sea de noche)
    revisar_comandos()

    if ahora.weekday() <= 4 and 11 <= ahora.hour < 17:
        token = obtener_token()
        if token:
            tasa, plazo = consultar_tasa_dinamica(token) # <- Actualizado acá
            if tasa:
                for nivel in reversed(INTERES_TASAS):
                    if tasa >= nivel:
                        if ultimo_umbral_avisado != nivel:
                            enviar_telegram(f"💰 *ALERTA*: Tasa en *{tasa}%* a {plazo} días (Nivel {nivel}%)")
                            ultimo_umbral_avisado = nivel
                        break
                if tasa < (ultimo_umbral_avisado - 5): ultimo_umbral_avisado = 0
        time.sleep(60) # Revisamos tasa cada minuto, pero comandos más seguido
    else:
        time.sleep(10) # Fuera de hora, revisamos comandos cada 10 seg.