from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from math import ceil
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qsl, urlsplit

from crous_search import validate_search_url
from crous_personal.config import MIN_CHECK_INTERVAL_SECONDS


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class PersonalStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise ValueError("Version de base inconnue ; aucun changement effectué.")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (
                profile_key TEXT PRIMARY KEY, config_json TEXT NOT NULL,
                last_signature TEXT, revision INTEGER NOT NULL DEFAULT 0,
                last_valid_check_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, profile_key TEXT NOT NULL REFERENCES profiles(profile_key),
                checked_at TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
                public_count INTEGER, evidence TEXT NOT NULL, source_url TEXT NOT NULL,
                fingerprint TEXT NOT NULL, http_status INTEGER, audience TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS checks_profile ON checks(profile_key, id DESC);
            CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT, profile_key TEXT NOT NULL REFERENCES profiles(profile_key),
                check_id INTEGER NOT NULL REFERENCES checks(id), revision INTEGER NOT NULL,
                kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT,
                UNIQUE(profile_key, revision)
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                key TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('watching','interested','applied','dismissed')),
                note TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        if version < 2:
            try:
                self.db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS offer_scopes (
                        profile_key TEXT NOT NULL REFERENCES profiles(profile_key), audience TEXT NOT NULL,
                        latest_observed_at TEXT, latest_snapshot_id INTEGER,
                        PRIMARY KEY(profile_key,audience)
                    );
                    CREATE TABLE IF NOT EXISTS offer_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_key TEXT NOT NULL REFERENCES profiles(profile_key),
                        audience TEXT NOT NULL, observed_at TEXT NOT NULL, imported_at TEXT NOT NULL,
                        status TEXT NOT NULL, source_url TEXT NOT NULL, total_count INTEGER,
                        import_key TEXT NOT NULL, stale INTEGER NOT NULL, data_json TEXT NOT NULL,
                        UNIQUE(profile_key,audience,import_key)
                    );
                    CREATE INDEX IF NOT EXISTS snapshots_scope ON offer_snapshots(profile_key,audience,observed_at DESC);
                    CREATE TABLE IF NOT EXISTS tracked_offers (
                        profile_key TEXT NOT NULL REFERENCES profiles(profile_key), audience TEXT NOT NULL,
                        offer_key TEXT NOT NULL, data_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        active INTEGER NOT NULL, match_state TEXT NOT NULL, match_reason TEXT NOT NULL,
                        arrival_status TEXT NOT NULL, available_from TEXT,
                        first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                        last_changed_at TEXT NOT NULL, disappeared_at TEXT,
                        revision INTEGER NOT NULL, last_snapshot_id INTEGER NOT NULL REFERENCES offer_snapshots(id),
                        PRIMARY KEY(profile_key,audience,offer_key)
                    );
                    CREATE INDEX IF NOT EXISTS offers_scope ON tracked_offers(profile_key,audience,active,match_state);
                    CREATE TABLE IF NOT EXISTS offer_revisions (
                        profile_key TEXT NOT NULL, audience TEXT NOT NULL, offer_key TEXT NOT NULL,
                        revision INTEGER NOT NULL, snapshot_id INTEGER NOT NULL REFERENCES offer_snapshots(id),
                        observed_at TEXT NOT NULL, active INTEGER NOT NULL, data_json TEXT NOT NULL,
                        match_state TEXT NOT NULL, match_reason TEXT NOT NULL, arrival_status TEXT NOT NULL,
                        PRIMARY KEY(profile_key,audience,offer_key,revision)
                    );
                    CREATE TABLE IF NOT EXISTS offer_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_key TEXT NOT NULL,
                        audience TEXT NOT NULL, offer_key TEXT NOT NULL,
                        snapshot_id INTEGER NOT NULL REFERENCES offer_snapshots(id), revision INTEGER NOT NULL,
                        kind TEXT NOT NULL, change_kind TEXT NOT NULL, match_state TEXT NOT NULL,
                        match_reason TEXT NOT NULL, created_at TEXT NOT NULL,
                        changed_fields_json TEXT NOT NULL, previous_json TEXT, current_json TEXT,
                        UNIQUE(profile_key,audience,offer_key,revision)
                    );
                    CREATE INDEX IF NOT EXISTS events_scope ON offer_events(profile_key,audience,id DESC);
                    PRAGMA user_version=2;
                    COMMIT;
                """)
            except BaseException:
                self.db.rollback()
                self.db.close()
                raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def claim_check(self, now=None):
        """Persisted cooldown shared by all searches/processes using this DB."""
        now = time.time() if now is None else now
        with self.transaction():
            row = self.db.execute("SELECT value FROM settings WHERE key='network_next_allowed_at'").fetchone()
            next_allowed = float(row[0]) if row else 0
            if now < next_allowed:
                return max(1, ceil(next_allowed - now))
            self.db.execute("INSERT INTO settings(key,value) VALUES('network_next_allowed_at',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now + MIN_CHECK_INTERVAL_SECONDS),))
        return 0

    def record(self, config, observation):
        if (observation.state not in ("UNKNOWN", "NO_PUBLIC_RESULTS")
                or observation.audience != "PUBLIC_ONLY" or observation.source_url != config.source_url
                or observation.public_count != (0 if observation.state == "NO_PUBLIC_RESULTS" else None)):
            raise ValueError("Observation incompatible avec la source publique vérifiée.")
        profile_key = config.profile_key
        signature = f"{observation.state}:{observation.reason}:{observation.fingerprint}"
        with self.transaction():
            self.db.execute("INSERT INTO profiles(profile_key,config_json) VALUES(?,?) "
                            "ON CONFLICT(profile_key) DO UPDATE SET config_json=excluded.config_json",
                            (profile_key, json.dumps(config.as_dict(), ensure_ascii=False)))
            profile = self.db.execute("SELECT * FROM profiles WHERE profile_key=?", (profile_key,)).fetchone()
            changed = profile["last_signature"] != signature
            revision = profile["revision"] + (1 if changed else 0)
            cursor = self.db.execute(
                "INSERT INTO checks(profile_key,checked_at,state,reason,public_count,evidence,source_url,fingerprint,http_status,audience) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (profile_key, observation.checked_at, observation.state, observation.reason,
                 observation.public_count, observation.evidence, observation.source_url,
                 observation.fingerprint, observation.http_status, observation.audience))
            check_id = cursor.lastrowid
            notice_id = None
            if changed and observation.state == "UNKNOWN":
                source_error = observation.reason in ("SOURCE_UNREACHABLE", "SOURCE_RATE_LIMITED")
                message = ("La source est inaccessible ; dernier contrôle fiable conservé."
                           if source_error else
                           "Le résultat public a changé ou demande une vérification manuelle. Aucune chambre n'est confirmée.")
                notice = self.db.execute(
                    "INSERT INTO notices(profile_key,check_id,revision,kind,message,created_at) VALUES(?,?,?,?,?,?)",
                    (profile_key, check_id, revision, "SOURCE_ERROR" if source_error else "REVIEW_REQUIRED", message, observation.checked_at))
                notice_id = notice.lastrowid
            last_valid = check_id if observation.state == "NO_PUBLIC_RESULTS" else profile["last_valid_check_id"]
            self.db.execute("UPDATE profiles SET last_signature=?,revision=?,last_valid_check_id=? WHERE profile_key=?",
                            (signature, revision, last_valid, profile_key))
        return {"check_id": check_id, "changed": changed, "new_notice_id": notice_id,
                "observation": observation.as_dict()}

    def status(self, profile_key):
        latest = self.db.execute("SELECT * FROM checks WHERE profile_key=? ORDER BY id DESC LIMIT 1", (profile_key,)).fetchone()
        valid = self.db.execute("SELECT c.* FROM profiles p JOIN checks c ON c.id=p.last_valid_check_id WHERE p.profile_key=?",
                                (profile_key,)).fetchone()
        unread = self.db.execute("SELECT COUNT(*) FROM notices WHERE profile_key=? AND read_at IS NULL", (profile_key,)).fetchone()[0]
        return {"last_check": dict(latest) if latest else None,
                "last_reliable_public_check": dict(valid) if valid else None,
                "unread_notices": unread}

    def history(self, profile_key, limit=20):
        if limit is None:
            return [dict(row) for row in self.db.execute("SELECT * FROM checks WHERE profile_key=? ORDER BY id DESC", (profile_key,))]
        return [dict(row) for row in self.db.execute("SELECT * FROM checks WHERE profile_key=? ORDER BY id DESC LIMIT ?",
                                                   (profile_key, min(max(int(limit), 1), 1000)))]

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == self.path or destination.exists():
            raise ValueError("La sauvegarde doit être un nouveau fichier distinct de la base.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
        finally:
            target.close()
        return str(destination)

    def notices(self, profile_key, include_read=False):
        sql = "SELECT * FROM notices WHERE profile_key=?" + ("" if include_read else " AND read_at IS NULL") + " ORDER BY id DESC"
        return [dict(row) for row in self.db.execute(sql, (profile_key,))]

    def acknowledge(self, profile_key, notice_ids):
        ids = list(dict.fromkeys(int(value) for value in notice_ids))
        if not ids or any(value < 1 for value in ids):
            raise ValueError("Indique au moins un identifiant positif de notification locale.")
        with self.transaction():
            count = 0
            for notice_id in ids:
                count += self.db.execute("UPDATE notices SET read_at=? WHERE id=? AND profile_key=? AND read_at IS NULL",
                                         (utc_now(), notice_id, profile_key)).rowcount
        return count

    def add_watch(self, url, title, note="", status="watching"):
        url = validate_search_url(url)
        if status not in ("watching", "interested", "applied", "dismissed"):
            raise ValueError("Statut de suivi inconnu.")
        for text, limit in ((title, 200), (note, 2000)):
            if not isinstance(text, str) or len(text) > limit or any(ord(c) < 32 and c not in "\n\t" for c in text):
                raise ValueError("Titre ou note invalide.")
        if not title.strip():
            raise ValueError("Un titre de suivi est requis.")
        key = hashlib.sha256(url.encode()).hexdigest()
        now = utc_now()
        with self.transaction():
            # Adding an already tracked link never erases its notes or status.
            self.db.execute("INSERT OR IGNORE INTO watchlist(key,url,title,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                            (key, url, title.strip(), status, note, now, now))
        return dict(self.db.execute("SELECT * FROM watchlist WHERE key=?", (key,)).fetchone())

    def watches(self, include_dismissed=False):
        sql = "SELECT * FROM watchlist" + ("" if include_dismissed else " WHERE status!='dismissed'") + " ORDER BY updated_at DESC,key"
        return [dict(row) for row in self.db.execute(sql)]

    def update_watch(self, key_prefix, status=None, note=None):
        if not re.fullmatch(r"[0-9a-f]{6,64}", key_prefix):
            raise ValueError("Identifiant de suivi invalide (au moins six caractères).")
        rows = self.db.execute("SELECT * FROM watchlist WHERE key LIKE ?", (key_prefix + "%",)).fetchall()
        if len(rows) != 1:
            raise ValueError("Suivi introuvable ou préfixe ambigu.")
        row = rows[0]
        if status is not None and status not in ("watching", "interested", "applied", "dismissed"):
            raise ValueError("Statut de suivi inconnu.")
        if note is not None and (not isinstance(note, str) or len(note) > 2000 or "\x00" in note):
            raise ValueError("Note invalide.")
        with self.transaction():
            self.db.execute("UPDATE watchlist SET status=?,note=?,updated_at=? WHERE key=?",
                            (status or row["status"], row["note"] if note is None else note, utc_now(), row["key"]))
        return dict(self.db.execute("SELECT * FROM watchlist WHERE key=?", (row["key"],)).fetchone())

    @staticmethod
    def _audience(audience):
        from crous_personal.offers import AUDIENCES
        if not isinstance(audience, str) or audience not in AUDIENCES:
            raise ValueError("Audience requise : PUBLIC_ONLY ou AUTHENTICATED.")
        return audience

    @staticmethod
    def _source_identity(url):
        parsed = urlsplit(validate_search_url(url))
        return parsed.netloc, parsed.path.rstrip("/"), tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))

    def _save_offer_revision(self, config, snapshot, snapshot_id, offer_key, revision,
                             data, previous, match, active, change_kind, previous_match=None):
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        self.db.execute(
            "INSERT INTO offer_revisions(profile_key,audience,offer_key,revision,snapshot_id,observed_at,active,data_json,match_state,match_reason,arrival_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (config.profile_key, snapshot.audience, offer_key, revision, snapshot_id, snapshot.observed_at,
             int(active), encoded, match["match_state"], match["match_reason"], match["arrival_status"]))
        kind = None
        if match["match_state"] == "UNKNOWN":
            kind = "REVIEW_REQUIRED"
        elif match["match_state"] == "MATCH":
            kind = "MATCH_CONFIRMED" if change_kind == "UPDATED" and previous_match != "MATCH" else change_kind + "_MATCH"
        elif previous_match == "MATCH" and change_kind == "UPDATED":
            kind = "NO_LONGER_MATCHES"
        if kind is None:
            return None
        old = previous or {}
        changed_fields = sorted(key for key in set(old) | set(data)
                                if key != "observed_at" and old.get(key) != data.get(key))
        if change_kind == "REMOVED" or change_kind == "REAPPEARED":
            changed_fields.append("active")
        cursor = self.db.execute(
            "INSERT INTO offer_events(profile_key,audience,offer_key,snapshot_id,revision,kind,change_kind,match_state,match_reason,created_at,changed_fields_json,previous_json,current_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (config.profile_key, snapshot.audience, offer_key, snapshot_id, revision, kind, change_kind,
             match["match_state"], match["match_reason"], snapshot.observed_at, json.dumps(changed_fields),
             json.dumps(previous, ensure_ascii=False) if previous is not None else None,
             encoded if active else None))
        return cursor.lastrowid

    def import_offers(self, config, snapshot):
        """Apply a validated snapshot atomically; scopes and timestamps never mix.

        Same-file imports are idempotent. A different payload at an older or equal
        observation time is retained for audit but cannot replace current state.
        Only COMPLETE, confirmed, count-consistent snapshots can remove rows.
        """
        from crous_personal.importers import OfferSnapshot
        from crous_personal.offers import filter_offer
        if not isinstance(snapshot, OfferSnapshot):
            raise ValueError("Un snapshot typé validé est requis.")
        if (self._source_identity(snapshot.source_url) != self._source_identity(config.source_url)
                or snapshot.zone.casefold() != config.zone.casefold()):
            raise ValueError("La recherche collectée diffère de la configuration personnelle.")
        result = {"snapshot_id": None, "audience": snapshot.audience, "status": snapshot.status,
                  "duplicate_import": False, "stale_import": False, "new": 0, "updated": 0,
                  "reappeared": 0, "removed": 0, "matched": 0, "review_required": 0, "event_ids": []}
        matches = {offer.offer_key: filter_offer(config, offer) for offer in snapshot.offers}
        result["matched"] = sum(item["match_state"] == "MATCH" for item in matches.values())
        with self.transaction():
            self.db.execute("INSERT INTO profiles(profile_key,config_json) VALUES(?,?) "
                            "ON CONFLICT(profile_key) DO UPDATE SET config_json=excluded.config_json",
                            (config.profile_key, json.dumps(config.as_dict(), ensure_ascii=False)))
            existing = self.db.execute("SELECT id,stale FROM offer_snapshots WHERE profile_key=? AND audience=? AND import_key=?",
                                       (config.profile_key, snapshot.audience, snapshot.import_key)).fetchone()
            if existing:
                result.update(snapshot_id=existing["id"], duplicate_import=True, stale_import=bool(existing["stale"]))
                return result
            self.db.execute("INSERT OR IGNORE INTO offer_scopes(profile_key,audience) VALUES(?,?)",
                            (config.profile_key, snapshot.audience))
            scope = self.db.execute("SELECT * FROM offer_scopes WHERE profile_key=? AND audience=?",
                                    (config.profile_key, snapshot.audience)).fetchone()
            stale = scope["latest_observed_at"] is not None and snapshot.observed_at <= scope["latest_observed_at"]
            cursor = self.db.execute(
                "INSERT INTO offer_snapshots(profile_key,audience,observed_at,imported_at,status,source_url,total_count,import_key,stale,data_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (config.profile_key, snapshot.audience, snapshot.observed_at, utc_now(), snapshot.status,
                 snapshot.source_url, snapshot.total_count, snapshot.import_key, int(stale), json.dumps(snapshot.as_dict(), ensure_ascii=False)))
            snapshot_id = cursor.lastrowid
            result.update(snapshot_id=snapshot_id, stale_import=stale)
            if stale:
                return result
            for offer in snapshot.offers:
                key, data, match = offer.offer_key, offer.as_dict(), matches[offer.offer_key]
                old = self.db.execute("SELECT * FROM tracked_offers WHERE profile_key=? AND audience=? AND offer_key=?",
                                      (config.profile_key, snapshot.audience, key)).fetchone()
                if old is not None and offer.observed_at < old["last_seen_at"]:
                    continue
                previous = json.loads(old["data_json"]) if old is not None else None
                changed = (old is None or not old["active"] or old["fingerprint"] != offer.fingerprint
                           or any(old[name] != match[name] for name in ("match_state", "match_reason", "arrival_status")))
                revision = (old["revision"] if old is not None else 0) + int(changed)
                change = "NEW" if old is None else "REAPPEARED" if not old["active"] else "UPDATED"
                first_seen = old["first_seen_at"] if old is not None else offer.observed_at
                changed_at = snapshot.observed_at if changed else old["last_changed_at"]
                self.db.execute(
                    "INSERT INTO tracked_offers(profile_key,audience,offer_key,data_json,fingerprint,active,match_state,match_reason,arrival_status,available_from,first_seen_at,last_seen_at,last_changed_at,disappeared_at,revision,last_snapshot_id) "
                    "VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(profile_key,audience,offer_key) DO UPDATE SET "
                    "data_json=excluded.data_json,fingerprint=excluded.fingerprint,active=1,match_state=excluded.match_state,match_reason=excluded.match_reason,arrival_status=excluded.arrival_status,available_from=excluded.available_from,"
                    "last_seen_at=excluded.last_seen_at,last_changed_at=excluded.last_changed_at,disappeared_at=NULL,revision=excluded.revision,last_snapshot_id=excluded.last_snapshot_id",
                    (config.profile_key, snapshot.audience, key, json.dumps(data, ensure_ascii=False), offer.fingerprint,
                     match["match_state"], match["match_reason"], match["arrival_status"], offer.available_from,
                     first_seen, offer.observed_at, changed_at, revision, snapshot_id))
                if changed:
                    result[{"NEW": "new", "UPDATED": "updated", "REAPPEARED": "reappeared"}[change]] += 1
                    event_id = self._save_offer_revision(config, snapshot, snapshot_id, key, revision, data,
                                                        previous, match, True, change, old["match_state"] if old else None)
                    if event_id is not None:
                        result["event_ids"].append(event_id)
                        result["review_required"] += int(match["match_state"] == "UNKNOWN")
            if snapshot.status == "COMPLETE":
                present = set(matches)
                active = self.db.execute("SELECT * FROM tracked_offers WHERE profile_key=? AND audience=? AND active=1",
                                         (config.profile_key, snapshot.audience)).fetchall()
                for old in active:
                    if old["offer_key"] in present:
                        continue
                    revision = old["revision"] + 1
                    data = json.loads(old["data_json"])
                    match = {name: old[name] for name in ("match_state", "match_reason", "arrival_status")}
                    self.db.execute("UPDATE tracked_offers SET active=0,revision=?,last_changed_at=?,disappeared_at=?,last_snapshot_id=? WHERE profile_key=? AND audience=? AND offer_key=?",
                                    (revision, snapshot.observed_at, snapshot.observed_at, snapshot_id,
                                     config.profile_key, snapshot.audience, old["offer_key"]))
                    result["removed"] += 1
                    event_id = self._save_offer_revision(config, snapshot, snapshot_id, old["offer_key"], revision,
                                                        data, data, match, False, "REMOVED", old["match_state"])
                    if event_id is not None:
                        result["event_ids"].append(event_id)
                        result["review_required"] += int(match["match_state"] == "UNKNOWN")
            self.db.execute("UPDATE offer_scopes SET latest_observed_at=?,latest_snapshot_id=? WHERE profile_key=? AND audience=?",
                            (snapshot.observed_at, snapshot_id, config.profile_key, snapshot.audience))
        return result

    def offers(self, profile_key, audience, include_inactive=False):
        audience = self._audience(audience)
        sql = "SELECT * FROM tracked_offers WHERE profile_key=? AND audience=?" + ("" if include_inactive else " AND active=1")
        sql += " ORDER BY active DESC,CASE match_state WHEN 'MATCH' THEN 0 WHEN 'UNKNOWN' THEN 1 ELSE 2 END,CASE arrival_status WHEN 'DATE_REACHED' THEN 0 WHEN 'DATE_UNCONFIRMED' THEN 1 ELSE 2 END,available_from,last_seen_at DESC,offer_key"
        result = []
        for row in self.db.execute(sql, (profile_key, audience)):
            result.append({**json.loads(row["data_json"]), **{name: row[name] for name in (
                "offer_key", "match_state", "match_reason", "arrival_status", "first_seen_at", "last_seen_at",
                "last_changed_at", "disappeared_at", "revision", "last_snapshot_id")}, "active": bool(row["active"])})
        return result

    def offer_events(self, profile_key, audience, limit=100):
        audience = self._audience(audience)
        sql = "SELECT * FROM offer_events WHERE profile_key=? AND audience=? ORDER BY id DESC"
        params = (profile_key, audience)
        if limit is not None:
            sql += " LIMIT ?"
            params += (min(max(int(limit), 1), 1000),)
        rows = self.db.execute(sql, params)
        result = []
        for row in rows:
            item = dict(row)
            for source, target in (("changed_fields_json", "changed_fields"), ("previous_json", "previous"), ("current_json", "current")):
                value = item.pop(source)
                item[target] = json.loads(value) if value is not None else None
            result.append(item)
        return result

    def offer_history(self, profile_key, audience, offer_key, limit=100):
        audience = self._audience(audience)
        sql = "SELECT * FROM offer_revisions WHERE profile_key=? AND audience=? AND offer_key=? ORDER BY revision DESC"
        params = (profile_key, audience, offer_key)
        if limit is not None:
            sql += " LIMIT ?"
            params += (min(max(int(limit), 1), 1000),)
        rows = self.db.execute(sql, params)
        return [{**{key: row[key] for key in row.keys() if key != "data_json"},
                 "active": bool(row["active"]), "offer": json.loads(row["data_json"])} for row in rows]
