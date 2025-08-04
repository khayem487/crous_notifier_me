import time
import requests
import threading
from playwright.sync_api import sync_playwright
from plyer import notification
import platform
import os
import msvcrt

# === CONFIGURATION ===
CROUS_ZONES = [
    ("https://trouverunlogement.lescrous.fr/tools/41/search?bounds=2.0362453_49.0338281_2.0845719_49.00172", "95000"),
    ("https://trouverunlogement.lescrous.fr/tools/41/search?bounds=1.9999694_49.0564525_2.0911198_49.017998", "cergy"),
    ("https://trouverunlogement.lescrous.fr/tools/41/search?bounds=2.224122_48.902156_2.4697602_48.8155755", "paris"),
    ("https://trouverunlogement.lescrous.fr/tools/41/search?bounds=2.0721836_49.0731128_2.1270334_49.024178", "pontoise")
]
CHECK_INTERVAL = 150
TELEGRAM_BOT_TOKEN = "7419377967:AAF3v-oUKBhjIaGbmGk7eAi6YErzGkyoLvc"
TELEGRAM_CHAT_ID = "6053608629"
SOUND_FILE = "beep.wav"

pause_search = False
mute_alerts = False
skip_zone = {zone[1]: False for zone in CROUS_ZONES}

def send_telegram_message(url, zone_name):
    if mute_alerts:
        return
    message = f"🔔 Logement CROUS détecté ({zone_name.upper()}) ! Vérifie vite : {url}"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(api_url, data=payload)
        print(f"[✔] Notification Telegram envoyée pour {zone_name}.")
    except Exception as e:
        print(f"[!] Erreur Telegram ({zone_name}) :", e)

def show_notification(zone_name):
    if mute_alerts:
        return
    notification.notify(
        title=f"🏠 CROUS - Logement à {zone_name.upper()}",
        message="Un logement est disponible, clique pour voir !",
        timeout=10
    )

def play_sound():
    if mute_alerts:
        return
    if os.path.exists(SOUND_FILE):
        if platform.system() == "Windows":
            import winsound
            winsound.PlaySound(SOUND_FILE, winsound.SND_FILENAME)
        else:
            os.system(f"afplay {SOUND_FILE}" if platform.system() == "Darwin" else f"aplay {SOUND_FILE}")
    else:
        print("[!] Fichier sonore introuvable, son ignoré.")

def aucun_logement(html):
    return "Aucun logement trouvé" in html or "Aucune résidence disponible" in html

def print_zone_status():
    print("📋 État des zones :")
    for i, (_, name) in enumerate(CROUS_ZONES, start=1):
        state = "⛔ désactivée" if skip_zone[name] else "✅ active"
        print(f" [{i}] {name.upper()}: {state}")

def input_listener():
    global pause_search, mute_alerts
    print("🎮 Commandes : [p] Pause | [m] Mute | [1-4] toggle zone | [z] zones | [q] quitter")
    while True:
        if msvcrt.kbhit():
            char = msvcrt.getch().decode().lower()
            if char == 'p':
                pause_search = not pause_search
                print("⏯️ Pause activée" if pause_search else "▶️ Reprise")
            elif char == 'm':
                mute_alerts = not mute_alerts
                print("🔇 Alertes désactivées" if mute_alerts else "🔔 Alertes activées")
            elif char in ['1', '2', '3', '4']:
                idx = int(char) - 1
                if 0 <= idx < len(CROUS_ZONES):
                    name = CROUS_ZONES[idx][1]
                    skip_zone[name] = not skip_zone[name]
                    print(f"{'⛔' if skip_zone[name] else '✅'} {name.upper()} {'désactivée' if skip_zone[name] else 'activée'}.")
            elif char == 'z':
                print_zone_status()
            elif char == 'q':
                print("👋 Fermeture du script...")
                os._exit(0)
        time.sleep(0.1)

def main_loop():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        threading.Thread(target=input_listener, daemon=True).start()

        while True:
            if pause_search:
                print("⏸️ Pause active...")
                time.sleep(CHECK_INTERVAL)
                continue

            for url, zone_name in CROUS_ZONES:
                if skip_zone[zone_name]:
                    print(f"⛔ {zone_name.upper()} ignorée")
                    continue
                try:
                    page.goto(url, timeout=60000)
                    time.sleep(5)
                    html = page.content()
                    if not aucun_logement(html):
                        print(f"🔔 Logement détecté ({zone_name.upper()})")
                        show_notification(zone_name)
                        play_sound()
                        send_telegram_message(url, zone_name)
                    else:
                        print(f"❌ Aucun logement ({zone_name.upper()})")
                except Exception as e:
                    print(f"[!] Erreur Playwright - {zone_name} :", e)
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main_loop()
