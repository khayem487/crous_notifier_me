import argparse
import json
import os
import time
import re
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

State = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
STATE_FILE = Path("state.json")

OFFICIAL_URL = "https://trouverunlogement.lescrous.fr/"
MAX_RESPONSE_BYTES = 1_000_000


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.lower().split())


def validate_source_url(url: str) -> str:
    """A public checker is not a generic proxy: only one fixed official page."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "trouverunlogement.lescrous.fr"
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Seule la page publique officielle prédéfinie est autorisée.")
    return OFFICIAL_URL


def analyze_text(text: str) -> dict:
    """Require explicit quantified evidence; navigation keywords are not proof."""
    text = normalize_text(text)
    def hypothetical(match):
        context = text[max(0, match.start() - 100):match.start()]
        return bool(re.search(r"\b(?:si|quand|lorsque|exemple|alerte|alertes|notification|notifications)\b[^.!?]{0,80}$", context))

    counts = [int(match.group(1)) for match in re.finditer(
        r"\b(\d{1,5})\s+(?:logements?|offres?)\s+(?:actuellement\s+)?(?:disponibles?|trouve(?:s|es)?)\b", text)
        if not hypothetical(match)]
    negative = any(not hypothetical(match) for match in re.finditer(
        r"\b(?:aucun logement|aucune offre|aucune place|0\s+(?:logements?|offres?|places?))\b"
        r"|\bplus de (?:logements?|offres?|places?)\s+disponibles?\b", text))
    positives = [count for count in counts if count > 0]
    if positives and (negative or 0 in counts or len(set(positives)) > 1):
        return {"state": "UNKNOWN", "count": None, "reason": "CONFLICTING_SIGNALS",
                "evidence": "La page contient des indications contradictoires ou plusieurs comptes distincts."}
    if positives:
        return {"state": "AVAILABLE", "count": positives[0], "reason": "EXPLICIT_COUNT",
                "evidence": f"Le texte reçu indique explicitement {positives[0]} offre(s) ou logement(s) disponible(s)."}
    if negative or (counts and all(count == 0 for count in counts)):
        return {"state": "UNAVAILABLE", "count": 0, "reason": "EXPLICIT_NO_RESULTS",
                "evidence": "Le texte reçu indique explicitement une absence d'offres ou de logements."}
    return {"state": "UNKNOWN", "count": None, "reason": "INSUFFICIENT_EVIDENCE",
            "evidence": "Aucun nombre explicite d'offres disponibles n'apparaît dans le texte reçu. Les mots de navigation ne prouvent pas une disponibilité."}


def load_config() -> dict:
    load_dotenv()
    return {
        "url": os.getenv("CROUS_URL", "https://trouverunlogement.lescrous.fr/").strip(),
        "interval": int(os.getenv("CHECK_INTERVAL_SEC", "300")),
        "timeout": int(os.getenv("REQUEST_TIMEOUT_SEC", "25")),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    }


def fetch_page_result(url: str = OFFICIAL_URL, timeout: int = 10) -> dict:
    url = validate_source_url(url)
    return _fetch_bounded_page_result(url, timeout)


def _fetch_bounded_page_result(url: str, timeout: int = 10) -> dict:
    """Shared transport; callers must validate their source before using it."""
    with requests.Session() as client:
        client.trust_env = False
        with client.get(url, headers={"User-Agent": "CrousNotifier/1.0 (manual public-page check)",
                                      "Accept": "text/html"}, timeout=timeout,
                        allow_redirects=False, stream=True) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("La page demande une redirection ; aucune autre adresse n'a été contactée.")
            response.raise_for_status()
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise ValueError("La source n'a pas retourné une page HTML.")
            body = bytearray()
            for chunk in response.iter_content(16_384):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("La réponse dépasse la taille autorisée.")
            soup = BeautifulSoup(bytes(body), "html.parser")
            for hidden in soup.select("script, style, template, header, nav, footer"):
                hidden.decompose()
            main = soup.find("main") or soup.body or soup
            return {"text": " ".join(main.get_text(" ", strip=True).split()),
                    "http_status": response.status_code, "source_url": url}


def fetch_page_text(url: str, timeout: int) -> str:
    """Keep the historical CLI's trusted CROUS_URL, including search queries.

    The public web app never calls this configurable fetcher. It uses only
    fetch_page_result(), whose fixed URL and redirect restrictions stay intact.
    """
    response = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for hidden in soup.select("script, style, template, header, nav, footer"):
        hidden.decompose()
    main = soup.find("main") or soup.body or soup
    return normalize_text(main.get_text(" ", strip=True))


def detect_state(text: str) -> State:
    return analyze_text(text)["state"]


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
    except Exception as error:
        print(f"[ERROR] Fetch impossible ({type(error).__name__})")
        return 1

    if state == "UNKNOWN":
        print("[WARN] Page reçue sans résultat fiable ; dernier état connu conservé, aucune alerte envoyée")
        return 1

    last = load_last_state()
    changed = state != last

    print(f"[INFO] Etat actuel: {state} (ancien: {last or 'NONE'})")

    if changed:
        msg = f"[CROUS] Changement detecte: {last or 'NONE'} -> {state}\n{cfg['url']}"
        if cfg["telegram_token"] and cfg["telegram_chat_id"]:
            try:
                sent = send_telegram(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
            except Exception as error:
                # Request exceptions can include the bot token in their URL.
                print(f"[WARN] Telegram non envoye ({type(error).__name__}) ; nouvelle tentative au prochain contrôle fiable")
                return 1
            if not sent:
                print("[WARN] Telegram non envoye ; nouvelle tentative au prochain contrôle fiable")
                return 1
            else:
                print("[INFO] Notification Telegram envoyee")
        else:
            print("[INFO] Telegram non configure, notification ignoree")
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
