"""Search configuration without inventing the official portal's query format."""

from dataclasses import dataclass
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from crous_notifier import OFFICIAL_URL, _fetch_bounded_page_result, analyze_text, normalize_text

ALLOWED_SOURCE_HOSTS = frozenset({"trouverunlogement.lescrous.fr"})
MAX_URL_LENGTH = 2048
SENSITIVE_QUERY_KEYS = frozenset({
    "token", "access_token", "id_token", "refresh_token", "password", "passwd",
    "secret", "client_secret", "authorization", "session", "sessionid", "session_id", "code",
    "ticket", "state", "auth", "auth_token", "samlrequest", "samlresponse", "cookie",
})


def validate_search_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Le lien officiel doit être une URL HTTPS sans caractère de contrôle.")
    value = value.strip()
    if not value or len(value) > MAX_URL_LENGTH or any(char.isspace() for char in value):
        raise ValueError("Le lien officiel est absent, trop long ou contient des espaces non encodés.")
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])", value):
        raise ValueError("Le lien officiel contient un encodage invalide.")
    try:
        parsed = urlsplit(value)
        fields = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=32)
    except ValueError:
        raise ValueError("Le lien officiel est invalide.") from None
    if (parsed.scheme != "https" or parsed.netloc not in ALLOWED_SOURCE_HOSTS
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError("Utilise un lien HTTPS du domaine trouverunlogement.lescrous.fr, sans identifiant, port ou fragment.")
    if any(key.lower() in SENSITIVE_QUERY_KEYS for key, _ in fields):
        raise ValueError("Utilise un lien public de recherche, sans jeton, session ou mot de passe.")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _label(value: str, name: str, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"Le champ {name} est invalide ou trop long.")
    return value.strip()


@dataclass(frozen=True)
class CrousSearch:
    source_url: str = OFFICIAL_URL
    zone: str = ""
    criteria: str = ""

    def __post_init__(self):
        object.__setattr__(self, "source_url", validate_search_url(self.source_url))
        object.__setattr__(self, "zone", _label(self.zone, "zone", 100))
        object.__setattr__(self, "criteria", _label(self.criteria, "critères", 300))
        if self.configured and not self.zone:
            raise ValueError("Indique la zone de la recherche officielle.")
        if not self.configured and (self.zone or self.criteria):
            raise ValueError("Copie d'abord le lien de la recherche officielle ; la page d'accueil n'applique aucun filtre.")

    @property
    def configured(self) -> bool:
        return self.source_url != OFFICIAL_URL

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) - {"source_url", "zone", "criteria"}:
            raise ValueError("La recherche doit contenir uniquement un lien officiel, une zone et des critères.")
        return cls(source_url=payload.get("source_url", OFFICIAL_URL),
                   zone=payload.get("zone", ""), criteria=payload.get("criteria", ""))

    def as_dict(self):
        return {"source_url": self.source_url, "zone": self.zone, "criteria": self.criteria}


def _contains_label(text: str, value: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(normalize_text(value)) + r"(?!\w)", normalize_text(text)))


def analyze_search_text(text: str, search: CrousSearch) -> dict:
    zone_confirmed = not search.zone or _contains_label(text, search.zone)
    criteria_confirmed = not search.criteria or _contains_label(text, search.criteria)
    scope = {"zone_confirmed_in_text": zone_confirmed,
             "criteria_confirmed_in_text": criteria_confirmed}
    if not zone_confirmed or not criteria_confirmed:
        return {"state": "UNKNOWN", "count": None, "reason": "SEARCH_SCOPE_NOT_CONFIRMED",
                "evidence": "Le texte reçu ne confirme pas la zone ou les critères indiqués. Aucun nombre de logements ne peut être attribué à cette recherche.",
                **scope}
    return {**analyze_text(text), **scope}


def fetch_search_page_result(search: CrousSearch, timeout: int = 10) -> dict:
    return _fetch_bounded_page_result(validate_search_url(search.source_url), timeout)
