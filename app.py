from collections import OrderedDict
from datetime import datetime, timezone
from math import ceil
from threading import Lock
from time import monotonic

from flask import Flask, jsonify, render_template, request

from crous_notifier import OFFICIAL_URL, fetch_page_result
from crous_search import CrousSearch, analyze_search_text, fetch_search_page_result

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8192
_cache = OrderedDict()
_next_fetch = 0.0
_lock = Lock()
CACHE_SECONDS = 120
MAX_CACHED_SOURCES = 16


@app.after_request
def public_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
    return response


@app.get("/")
def home():
    return render_template("index.html", official_url=OFFICIAL_URL)


@app.get("/health")
def health():
    return jsonify(status="ok", service="crous-notifier", mode="manual-public-check",
                   notifications_enabled=False, background_monitoring=False)


@app.route("/api/check", methods=["GET", "POST"])
def check():
    global _next_fetch
    if request.method == "GET":
        if set(request.args) - {"source"} or request.args.get("source", "official") != "official":
            return jsonify(error="Utilise le formulaire pour configurer une recherche officielle."), 400
        search = CrousSearch()
    else:
        try:
            if request.args:
                raise ValueError("Les paramètres de recherche doivent être envoyés dans le formulaire.")
            search = CrousSearch.from_payload(request.get_json(silent=True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
    with _lock:
        now = monotonic()
        cached = _cache.get(search.source_url)
        if cached is not None and now >= cached["expires"]:
            del _cache[search.source_url]
            cached = None
        if cached is None:
            if now < _next_fetch:
                retry_after = max(1, ceil(_next_fetch - now))
                response = jsonify(error=f"Un contrôle vient d'être effectué. Réessaie dans {retry_after} secondes.",
                                   retry_after_seconds=retry_after)
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                response.headers["Cache-Control"] = "no-store"
                return response
            # Cooldown is shared by all URLs on this process, including failures.
            _next_fetch = now + CACHE_SECONDS
            checked_at = datetime.now(timezone.utc).isoformat()
            try:
                page = (fetch_search_page_result(search, timeout=10) if search.configured
                        else fetch_page_result(OFFICIAL_URL, timeout=10))
                cached = {"page": page, "fetch_ok": True}
            except Exception:
                # No source body, exception detail, credentials, email or Telegram data are exposed.
                cached = {"fetch_ok": False}
            cached.update(checked_at=checked_at, expires=monotonic() + CACHE_SECONDS)
            _cache[search.source_url] = cached
            while len(_cache) > MAX_CACHED_SOURCES:
                _cache.popitem(last=False)
        else:
            _cache.move_to_end(search.source_url)
        if cached["fetch_ok"]:
            result = {**analyze_search_text(cached["page"]["text"], search), "fetch_ok": True,
                      "http_status": cached["page"]["http_status"]}
        else:
            result = {"state": "UNKNOWN", "count": None, "reason": "SOURCE_UNREACHABLE",
                      "fetch_ok": False, "http_status": None,
                      "evidence": "Le portail n'a pas fourni une page exploitable pendant cette vérification. Consulte directement la source officielle."}
        response = jsonify({**result, "source_url": search.source_url,
                            "search": search.as_dict(), "checked_at": cached["checked_at"],
                            "notifications_enabled": False, "background_monitoring": False,
                            "check_type": "configured-public-search" if search.configured else "single-public-page",
                            "reuse_seconds": CACHE_SECONDS})
        # Search inputs are reflected only to their requester, not cached by a CDN.
        response.headers["Cache-Control"] = "no-store"
        return response
