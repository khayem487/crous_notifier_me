"""Validated offer data, not a claim that a live positive parser was verified."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from crous_notifier import normalize_text
from crous_search import validate_search_url

AUDIENCES = frozenset({"PUBLIC_ONLY", "AUTHENTICATED"})
DETAIL_PATH = re.compile(r"/tools/([0-9]{1,10})/accommodations/([A-Za-z0-9][A-Za-z0-9._~-]{0,127})/?$")


def bounded_text(value, label, maximum, optional=False):
    if value is None and optional:
        return None
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"Champ {label} invalide.")
    value = value.strip()
    if not value:
        if optional:
            return None
        raise ValueError(f"Champ {label} requis.")
    return value


def timestamp(value):
    value = bounded_text(value, "observed_at", 40)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ValueError("Horodatage ISO 8601 invalide.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("L'horodatage doit indiquer un fuseau horaire.")
    try:
        parsed = parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError("Horodatage ISO 8601 hors limites.") from None
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Horodatage trop éloigné dans le futur.")
    return parsed.isoformat(timespec="microseconds")


def decimal_value(value, label, places=2, maximum="1000000"):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"Champ {label} invalide.")
    if len(str(value)) > 40:
        raise ValueError(f"Champ {label} trop long.")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"Champ {label} invalide.") from None
    if (not number.is_finite() or abs(number.as_tuple().exponent) > 8
            or number < 0 or number > Decimal(maximum)
            or number != number.quantize(Decimal(1).scaleb(-places))):
        raise ValueError(f"Champ {label} hors limites ou précision invalide.")
    return format(number.quantize(Decimal(1).scaleb(-places)), "f")


def canonical_offer_url(value):
    value = validate_search_url(value)
    parsed = urlsplit(value)
    match = DETAIL_PATH.fullmatch(parsed.path)
    if match is None or parsed.query:
        raise ValueError("Un lien officiel de détail d'annonce sans paramètre est requis.")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class Offer:
    url: str
    title: str
    observed_at: str
    audience: str
    offer_id: str | None = None
    city: str | None = None
    housing_type: str | None = None
    rent_eur: str | None = None
    rent_min_eur: str | None = None
    rent_max_eur: str | None = None
    surface_m2: str | None = None
    residence: str | None = None
    available_from: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "url", canonical_offer_url(self.url))
        url_id = DETAIL_PATH.fullmatch(urlsplit(self.url).path).group(2)
        if self.offer_id is not None and (isinstance(self.offer_id, bool) or not isinstance(self.offer_id, (str, int))):
            raise ValueError("Identifiant d'annonce invalide.")
        offer_id = url_id if self.offer_id is None else bounded_text(str(self.offer_id), "offer_id", 128)
        if offer_id != url_id:
            raise ValueError("L'identifiant de l'annonce ne correspond pas à son lien.")
        object.__setattr__(self, "offer_id", offer_id)
        if not isinstance(self.audience, str) or self.audience not in AUDIENCES:
            raise ValueError("Audience d'annonce inconnue.")
        object.__setattr__(self, "observed_at", timestamp(self.observed_at))
        for name, maximum, optional in (("title", 300, False), ("city", 100, True),
                                        ("housing_type", 100, True), ("residence", 300, True)):
            object.__setattr__(self, name, bounded_text(getattr(self, name), name, maximum, optional))
        for name in ("rent_eur", "rent_min_eur", "rent_max_eur"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), name))
        object.__setattr__(self, "surface_m2", decimal_value(self.surface_m2, "surface_m2", 3, "10000"))
        low, high = self.rent_min_eur, self.rent_max_eur
        if low is not None and high is not None and Decimal(low) > Decimal(high):
            raise ValueError("Fourchette de loyer contradictoire.")
        if self.rent_eur is not None and ((low is not None and Decimal(self.rent_eur) < Decimal(low))
                                         or (high is not None and Decimal(self.rent_eur) > Decimal(high))):
            raise ValueError("Loyer exact incompatible avec sa fourchette.")
        if self.available_from is not None:
            value = bounded_text(self.available_from, "available_from", 10)
            try:
                parsed_date = date.fromisoformat(value)
            except ValueError:
                raise ValueError("Date d'entrée invalide.") from None
            if parsed_date.isoformat() != value or not 2000 <= parsed_date.year <= 2100:
                raise ValueError("Date d'entrée invalide.")

    @classmethod
    def from_payload(cls, payload, *, observed_at, audience):
        if not isinstance(payload, dict) or set(payload) - set(cls.__dataclass_fields__):
            raise ValueError("Champs d'annonce inconnus ou format invalide.")
        if "url" not in payload or "title" not in payload:
            raise ValueError("Lien et titre d'annonce requis.")
        values = {"observed_at": observed_at, "audience": audience, **payload}
        offer = cls(**values)
        if offer.audience != audience:
            raise ValueError("Audiences d'annonce et de collecte différentes.")
        if datetime.fromisoformat(offer.observed_at) > datetime.fromisoformat(timestamp(observed_at)) + timedelta(seconds=5):
            raise ValueError("Annonce observée après l'horodatage de collecte.")
        return offer

    @property
    def offer_key(self):
        path = DETAIL_PATH.fullmatch(urlsplit(self.url).path)
        return f"crous:{path.group(1)}:{path.group(2)}"

    @property
    def fingerprint(self):
        data = self.as_dict()
        data.pop("observed_at")
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def as_dict(self):
        return asdict(self)


def canonical_housing_type(value):
    if not value:
        return None
    text = normalize_text(value)
    if re.match(r"^(?:chambres?|rooms?)(?:\s|$)", text):
        return "chambre"
    if text in ("studio", "t1", "t1 bis", "t1bis", "t2", "t3", "t4", "t5", "appartement", "colocation"):
        return text
    # In particular, 'Individuel', a bed count or a residence name proves no type.
    return None


def filter_offer(config, offer):
    arrival = ("DATE_UNCONFIRMED" if offer.available_from is None else
               "FUTURE_DATE" if date.fromisoformat(offer.available_from) > date.today() else "DATE_REACHED")
    def result(state, reason):
        return {"match_state": state, "match_reason": reason, "arrival_status": arrival}
    if offer.city is None:
        return result("UNKNOWN", "CITY_UNCONFIRMED")
    if normalize_text(offer.city) != normalize_text(config.zone):
        return result("NO_MATCH", "OTHER_CITY")
    wanted, actual = canonical_housing_type(config.housing_type), canonical_housing_type(offer.housing_type)
    if wanted is None or actual is None:
        return result("UNKNOWN", "TYPE_UNCONFIRMED")
    if wanted != actual:
        return result("NO_MATCH", "OTHER_HOUSING_TYPE")
    if config.max_rent_eur is not None:
        budget = Decimal(config.max_rent_eur)
        if offer.rent_eur is not None:
            if Decimal(offer.rent_eur) > budget:
                return result("NO_MATCH", "ABOVE_BUDGET")
        elif offer.rent_min_eur is not None and Decimal(offer.rent_min_eur) > budget:
            return result("NO_MATCH", "ABOVE_BUDGET")
        elif offer.rent_max_eur is None or Decimal(offer.rent_max_eur) > budget:
            return result("UNKNOWN", "PRICE_UNCONFIRMED")
    return result("MATCH", "CRITERIA_MATCH_DATE_REQUIRES_REVIEW" if arrival != "DATE_REACHED" else "CRITERIA_MATCH")
