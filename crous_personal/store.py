from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from math import ceil
from pathlib import Path
import re
import sqlite3
import time

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
        if version not in (0, 1):
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
            PRAGMA user_version=1;
        """)

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
