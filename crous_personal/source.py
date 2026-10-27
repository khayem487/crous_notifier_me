from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import requests

from crous_notifier import _fetch_bounded_page_result, normalize_text
from crous_personal.config import PersonalConfig

PUBLIC_LIMIT = (
    "Recherche publique sans connexion : l'offre accessible peut dépendre du profil DSE. "
    "Un résultat public vide ne signifie pas qu'aucune chambre n'est accessible après connexion."
)


@dataclass(frozen=True)
class Observation:
    state: str
    reason: str
    checked_at: str
    source_url: str
    public_count: int | None
    evidence: str
    fingerprint: str
    http_status: int | None = None
    audience: str = "PUBLIC_ONLY"
    rooms_confirmed: bool = False

    def as_dict(self):
        return {**asdict(self), "limitation": PUBLIC_LIMIT}


def analyze_public_page(text: str, config: PersonalConfig, http_status: int = 200) -> Observation:
    normalized = normalize_text(text)
    zone = normalize_text(config.zone)
    query = parse_qs(urlsplit(config.source_url).query)
    source_zone = normalize_text(query.get("locationName", [""])[0])
    checked_at = datetime.now(timezone.utc).isoformat()
    fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    no_results = re.search(r"\baucun logement trouve pour " + re.escape(zone) + r"(?!\w)", normalized)
    # Reject a contradictory quantity instead of attributing it to a room type.
    contradictory = re.search(r"\b[1-9]\d*\s+logements?\s+(?:trouves?|disponibles?)\b", normalized)
    if source_zone == zone and no_results and not contradictory:
        return Observation("NO_PUBLIC_RESULTS", "OFFICIAL_EMPTY_PUBLIC_SEARCH", checked_at,
                           config.source_url, 0, "Aucun logement trouvé pour " + config.zone,
                           fingerprint, http_status)
    reason = "SOURCE_ZONE_NOT_CONFIRMED" if source_zone != zone else "RESULTS_REQUIRE_MANUAL_REVIEW"
    # No positive announcement structure has been observed and verified yet.
    return Observation("UNKNOWN", reason, checked_at, config.source_url, None,
                       "Le résultat ne permet pas de confirmer des chambres : ouvre la recherche officielle pour le vérifier.",
                       fingerprint, http_status)


def check_public_source(config: PersonalConfig) -> Observation:
    try:
        page = _fetch_bounded_page_result(config.source_url, config.request_timeout_seconds)
        return analyze_public_page(page["text"], config, page["http_status"])
    except (requests.RequestException, ValueError, OSError) as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        reason = "SOURCE_RATE_LIMITED" if status == 429 else "SOURCE_UNREACHABLE"
        signature = f"{reason}:{type(error).__name__}:{status}"
        return Observation("UNKNOWN", reason, datetime.now(timezone.utc).isoformat(),
                           config.source_url, None, "Le contrôle a échoué ; le dernier résultat fiable est conservé.",
                           hashlib.sha256(signature.encode()).hexdigest(), status)
