"""Strict snapshot-v1 import contract shared with the local browser collector.

This validates data, not the user's session: the collector must establish the
audience on the CROUS page itself. No cookie, HTML or profile fields are accepted.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from crous_notifier import normalize_text
from crous_search import validate_search_url
from crous_personal.offers import AUDIENCES, Offer, bounded_text, timestamp

MAX_IMPORT_BYTES = 2_000_000
MAX_OFFERS = 2000
FIELDS = {"schema_version", "source_url", "zone", "audience", "audience_confirmed", "observed_at",
          "status", "pagination_complete", "total_count", "reason", "offers"}


@dataclass(frozen=True)
class OfferSnapshot:
    source_url: str
    zone: str
    audience: str
    audience_confirmed: bool
    observed_at: str
    status: str
    pagination_complete: bool
    total_count: int | None
    offers: tuple
    reason: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Version de snapshot inconnue.")
        object.__setattr__(self, "source_url", validate_search_url(self.source_url))
        object.__setattr__(self, "zone", bounded_text(self.zone, "zone", 100))
        object.__setattr__(self, "observed_at", timestamp(self.observed_at))
        if not isinstance(self.audience, str) or self.audience not in AUDIENCES or type(self.audience_confirmed) is not bool:
            raise ValueError("Audience de collecte invalide.")
        if self.status not in ("COMPLETE", "PARTIAL", "UNKNOWN") or type(self.pagination_complete) is not bool:
            raise ValueError("État de collecte invalide.")
        if self.audience == "AUTHENTICATED" and not self.audience_confirmed:
            raise ValueError("Une session authentifiée doit être confirmée sur la page source.")
        if not self.audience_confirmed and self.status != "UNKNOWN":
            raise ValueError("Audience incertaine : le snapshot doit rester UNKNOWN.")
        parsed = urlsplit(self.source_url)
        path = re.fullmatch(r"/tools/([0-9]{1,10})/search/?", parsed.path)
        zones = parse_qs(parsed.query).get("locationName", [])
        if path is None or len(zones) != 1 or normalize_text(zones[0]) != normalize_text(self.zone):
            raise ValueError("Le lien de recherche ne confirme pas la zone de collecte.")
        if self.total_count is not None and (type(self.total_count) is not int or not 0 <= self.total_count <= 1_000_000):
            raise ValueError("Compteur de collecte invalide.")
        if not isinstance(self.offers, tuple) or len(self.offers) > MAX_OFFERS or any(not isinstance(item, Offer) for item in self.offers):
            raise ValueError("Liste d'annonces invalide ou trop volumineuse.")
        keys = set()
        for offer in self.offers:
            if offer.audience != self.audience or offer.offer_key.split(":")[1] != path.group(1):
                raise ValueError("Une annonce n'appartient pas à la portée de collecte.")
            if offer.offer_key in keys:
                raise ValueError("Annonce dupliquée dans le snapshot.")
            keys.add(offer.offer_key)
        if self.status == "UNKNOWN" and self.offers:
            raise ValueError("Une collecte UNKNOWN ne doit pas déclarer d'annonces.")
        if self.status == "UNKNOWN" and (self.total_count is not None or self.pagination_complete):
            raise ValueError("Une collecte UNKNOWN ne confirme ni compteur ni pagination.")
        if self.total_count is not None and self.total_count < len(keys):
            raise ValueError("Le compteur est inférieur au nombre d'annonces reçues.")
        if self.status == "COMPLETE" and (not self.pagination_complete or not self.audience_confirmed or self.total_count != len(keys)):
            raise ValueError("Une collecte COMPLETE exige une pagination et un compteur cohérents.")
        if self.reason is not None and (not isinstance(self.reason, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", self.reason)):
            raise ValueError("Code de diagnostic invalide.")

    @property
    def import_key(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def as_dict(self):
        return {"schema_version": 1, "source_url": self.source_url, "zone": self.zone,
                "audience": self.audience, "audience_confirmed": self.audience_confirmed,
                "observed_at": self.observed_at, "status": self.status,
                "pagination_complete": self.pagination_complete, "total_count": self.total_count,
                "reason": self.reason, "offers": [item.as_dict() for item in sorted(self.offers, key=lambda item: item.offer_key)]}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé JSON dupliquée.")
        result[key] = value
    return result


def parse_snapshot(payload):
    if isinstance(payload, (str, bytes)):
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(raw) > MAX_IMPORT_BYTES:
            raise ValueError("Import trop volumineux (maximum 2 Mo).")
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nombre JSON non fini.")))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise ValueError("Import JSON invalide.") from None
    if not isinstance(payload, dict) or set(payload) - FIELDS or FIELDS - {"reason"} - set(payload):
        raise ValueError("Champs de collecte inconnus ou requis manquants.")
    try:
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("Import trop volumineux (maximum 2 Mo).")
    except (TypeError, RecursionError, OverflowError):
        raise ValueError("Import JSON invalide.") from None
    if not isinstance(payload["offers"], list) or len(payload["offers"]) > MAX_OFFERS:
        raise ValueError("Liste d'annonces invalide ou trop volumineuse.")
    observed_at = timestamp(payload["observed_at"])
    offers = tuple(Offer.from_payload(item, observed_at=observed_at, audience=payload["audience"]) for item in payload["offers"])
    return OfferSnapshot(**{**payload, "observed_at": observed_at, "offers": offers})
