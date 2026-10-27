from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from crous_search import validate_search_url

MIN_CHECK_INTERVAL_SECONDS = 300
CERGY_PUBLIC_URL = (
    "https://trouverunlogement.lescrous.fr/tools/47/search?"
    "bounds=1.9999694_49.0564525_2.0911198_49.017998&locationName=Cergy"
)


@dataclass(frozen=True)
class PersonalConfig:
    zone: str = "Cergy"
    housing_type: str = "chambre"
    max_rent_eur: str | None = None
    move_in_preference: str = "asap"
    source_url: str = CERGY_PUBLIC_URL
    database: str = "data/crous-personal.sqlite3"
    interval_seconds: int = 300
    request_timeout_seconds: int = 15

    def __post_init__(self):
        if self.move_in_preference != "asap":
            raise ValueError("L'entrée est configurée dès disponibilité : move_in_preference=asap.")
        for name in ("zone", "housing_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
                raise ValueError(f"Configuration {name} invalide.")
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "source_url", validate_search_url(self.source_url))
        if self.max_rent_eur is not None:
            try:
                budget = Decimal(str(self.max_rent_eur))
            except (InvalidOperation, ValueError):
                raise ValueError("Le budget maximum doit être un nombre positif ou null.") from None
            if not budget.is_finite() or budget <= 0 or budget > 10000:
                raise ValueError("Le budget maximum doit être un nombre positif ou null.")
            object.__setattr__(self, "max_rent_eur", format(budget, "f"))
        if (type(self.interval_seconds) is not int or not MIN_CHECK_INTERVAL_SECONDS <= self.interval_seconds <= 86400
                or type(self.request_timeout_seconds) is not int or not 1 <= self.request_timeout_seconds <= 30):
            raise ValueError("Intervalle : 300 à 86400 secondes ; timeout : 1 à 30 secondes.")
        if not isinstance(self.database, str) or not self.database.strip() or "\x00" in self.database:
            raise ValueError("Le chemin de la base locale est invalide.")

    @property
    def profile_key(self):
        # Moving the database or changing a polling interval is not a new search.
        settings = {name: getattr(self, name) for name in ("zone", "housing_type", "max_rent_eur", "source_url")}
        return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def as_dict(self):
        return asdict(self)


def load_personal_config(path: Path) -> PersonalConfig:
    if path.stat().st_size > 16384:
        raise ValueError("Le fichier de configuration est trop volumineux.")
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = set(PersonalConfig.__dataclass_fields__)
    if not isinstance(data, dict) or set(data) - fields:
        raise ValueError("Le fichier contient des options inconnues.")
    return PersonalConfig(**data)
