import argparse
import json
import os
import time
from pathlib import Path
from typing import Literal

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

State = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
STATE_FILE = Path("state.json")

AVAILABLE_HINTS = [
    "disponible",
    "disponibilite",
    "disponibilité",
    "logements disponibles",
    "offres",
]

UNAVAILABLE_HINTS = [
    "aucune offre",
    "aucun logement",
    "complet",
    "plus de place",
    "indisponible",
]


def load_config() -> dict:
    load_dotenv()
    return {
        "url": os.getenv("CROUS_URL", "https://trouverunlogement.lescrous.fr/").strip(),
        "interval": int(os.getenv("CHECK_INTERVAL_SEC", "300")),
        "timeout": int(os.getenv("REQUEST_TIMEOUT_SEC", "25")),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    }


def fetch_page_text(url: str, timeout: int) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    res = requests.get(url, headers=headers, timeout=timeout)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split()).lower()


def detect_state(text: str) -> State:
    if any(x in text for x in UNAVAILABLE_HINTS):
        return "UNAVAILABLE"
    if any(x in text for x in AVAILABLE_HINTS):
        return "AVAILABLE"
    return "UNKNOWN"


def load_last_state() -> str:
    if not STATE_FILE.exists():
        return ""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("last_state", "")
    except Exception:
        return ""


def save_last_state(state: str) -> None:
    STATE_FILE.write_text(json.dumps({"last_state": state}, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    if not token or not chat_id:
        return False
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=15,
    ).raise_for_status()
    return True


def run_once(cfg: dict) -> int:
    if not cfg["url"]:
        print("[ERROR] CROUS_URL manquante. Configure .env")
        return 2

    try:
        text = fetch_page_text(cfg["url"], cfg["timeout"])
        state = detect_state(text)
    except Exception as e:
        print(f"[ERROR] Fetch impossible: {e}")
        return 1

    last = load_last_state()
    changed = state != last

    print(f"[INFO] Etat actuel: {state} (ancien: {last or 'NONE'})")

    if changed:
        msg = f"[CROUS] Changement detecte: {last or 'NONE'} -> {state}\n{cfg['url']}"
        try:
            sent = send_telegram(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
            if sent:
                print("[INFO] Notification Telegram envoyee")
            else:
                print("[INFO] Telegram non configure, notification ignoree")
        except Exception as e:
            print(f"[WARN] Telegram non envoye: {e}")
        save_last_state(state)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Crous availability notifier")
    parser.add_argument("--once", action="store_true", help="Run a single check")
    args = parser.parse_args()

    cfg = load_config()

    if args.once:
        raise SystemExit(run_once(cfg))

    print(f"[START] Monitoring {cfg['url']} every {cfg['interval']}s")
    while True:
        rc = run_once(cfg)
        if rc != 0:
            print("[WARN] check failed, retrying after interval")
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
