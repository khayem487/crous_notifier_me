import time
import requests
import threading
from playwright.sync_api import sync_playwright

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

pause = False
mute = False
active_zones = [True] * len(CROUS_ZONES)

def send_telegram_message(url, label):
    if not mute:
        message = f"🔔 Logement disponible à {label} ! Vérifie : {url}"
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message}
        )

def aucun_logement(html):
    return "Aucun logement trouvé" in html or "Aucune résidence disponible" in html or "Erreur de connexion"

def input_listener():
    global pause, mute
    while True:
        cmd = input("Commande (1-4=toggle zone, p=pause, m=mute, z=état, q=quitter) : ").strip().lower()
        if cmd == "p":
            pause = not pause
            print("⏸️ Pause activée" if pause else "▶️ Reprise")
        elif cmd == "m":
            mute = not mute
            print("🔕 Muet activé" if mute else "🔔 Notifications sonores activées")
        elif cmd == "z":
            print("📊 État des zones :")
            for i, (_, label) in enumerate(CROUS_ZONES):
                status = "✅" if active_zones[i] else "❌"
                print(f"{i+1}. {label} : {status}")
        elif cmd in {"1", "2", "3", "4"}:
            i = int(cmd) - 1
            active_zones[i] = not active_zones[i]
            print(f"🔄 Zone {CROUS_ZONES[i][1]} → {'active' if active_zones[i] else 'suspendue'}")
        elif cmd == "q":
            print("🛑 Arrêt demandé. Fin du script.")
            exit()

def main_loop():
    threading.Thread(target=input_listener, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        while True:
            if pause:
                print("⏸️ En pause...")
                time.sleep(5)
                continue
            for i, (url, label) in enumerate(CROUS_ZONES):
                if not active_zones[i]:
                    continue
                try:
                    page.goto(url, timeout=60000)
                    time.sleep(4)
                    html = page.content()
                    if not aucun_logement(html):
                        print(f"🔔 Logement détecté à {label} !")
                        send_telegram_message(url, label)
                    else:
                        print(f"❌ Aucun logement à {label}")
                except Exception as e:
                    print(f"[!] Erreur pour {label} : {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main_loop()
