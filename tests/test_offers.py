"""Synthetic positive records exercise the import contract, not a live parser."""

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from crous_personal.config import PersonalConfig
from crous_personal.importers import MAX_IMPORT_BYTES, parse_snapshot
from crous_personal.offers import Offer, filter_offer
from crous_personal.store import PersonalStore


def test_offer(number="test1", **changes):
    return {"offer_id": number, "url": f"https://trouverunlogement.lescrous.fr/tools/47/accommodations/{number}",
            "title": "Fixture TEST — aucune annonce réelle", "city": "Cergy", "housing_type": "chambre",
            "rent_eur": 350.45, "surface_m2": 9.5, "residence": "Résidence TEST", **changes}


def snapshot(offers=(), minute=0, audience="AUTHENTICATED", status="COMPLETE", **changes):
    return {"schema_version": 1, "source_url": PersonalConfig().source_url, "zone": "Cergy",
            "audience": audience, "audience_confirmed": True,
            "observed_at": f"2026-10-26T10:{minute:02d}:00Z", "status": status,
            "pagination_complete": status == "COMPLETE",
            "total_count": None if status == "UNKNOWN" else len(offers), "offers": list(offers), **changes}


class OfferValidationTests(unittest.TestCase):
    def test_euros_float_normalization_does_not_convert_centimes_again(self):
        offer = parse_snapshot(snapshot([test_offer(rent_eur=325.4)])).offers[0]
        self.assertEqual(offer.rent_eur, "325.40")
        self.assertEqual(offer.surface_m2, "9.500")
        self.assertEqual(offer.offer_key, "crous:47:test1")
        self.assertEqual(offer.offer_id, "test1")

    def test_type_city_and_price_are_not_inferred_from_title_residence_or_cohabitation(self):
        config = PersonalConfig()
        for changes, expected in [({"city": None}, "CITY_UNCONFIRMED"),
                                  ({"housing_type": None}, "TYPE_UNCONFIRMED"),
                                  ({"housing_type": "Individuel"}, "TYPE_UNCONFIRMED")]:
            offer = parse_snapshot(snapshot([test_offer(**changes)])).offers[0]
            self.assertEqual(filter_offer(config, offer)["match_state"], "UNKNOWN")
            self.assertEqual(filter_offer(config, offer)["match_reason"], expected)

    def test_explicit_room_matches_without_price_cap_and_other_types_or_cities_do_not(self):
        config = PersonalConfig()
        for changes, state in [({"rent_eur": 20000}, "MATCH"), ({"rent_eur": None}, "MATCH"),
                               ({"housing_type": "studio"}, "NO_MATCH"), ({"city": "Pontoise"}, "NO_MATCH")]:
            with self.subTest(changes=changes):
                offer = parse_snapshot(snapshot([test_offer(**changes)])).offers[0]
                self.assertEqual(filter_offer(config, offer)["match_state"], state)

    def test_optional_budget_compares_decimal_exact_or_range_without_claiming_unknown_prices(self):
        config = replace(PersonalConfig(), max_rent_eur="350.45")
        for changes, state in [({"rent_eur": "350.45"}, "MATCH"), ({"rent_eur": "350.46"}, "NO_MATCH"),
                               ({"rent_eur": None}, "UNKNOWN"),
                               ({"rent_eur": None, "rent_min_eur": "300", "rent_max_eur": "350"}, "MATCH"),
                               ({"rent_eur": None, "rent_min_eur": "300", "rent_max_eur": "400"}, "UNKNOWN")]:
            with self.subTest(changes=changes):
                offer = parse_snapshot(snapshot([test_offer(**changes)])).offers[0]
                self.assertEqual(filter_offer(config, offer)["match_state"], state)

    def test_unknown_arrival_date_is_preserved_without_pretending_asap_is_verified(self):
        offer = parse_snapshot(snapshot([test_offer(available_from=None)])).offers[0]
        result = filter_offer(PersonalConfig(), offer)
        self.assertEqual(result["arrival_status"], "DATE_UNCONFIRMED")
        self.assertEqual(result["match_state"], "MATCH")
        self.assertIsNone(offer.available_from)

    def test_malformed_private_or_conflicting_data_is_rejected(self):
        bad = [{"url": "https://localhost/tools/47/accommodations/test1"},
               {"url": "https://trouverunlogement.lescrous.fr/tools/47/accommodations/test1?ticket=secret"},
               {"offer_id": "different"}, {"offer_id": True}, {"housing_type": ["chambre"]},
               {"title": "x" * 301}, {"rent_eur": -1}, {"rent_eur": "NaN"}, {"rent_eur": "0E-9999999"},
               {"rent_eur": "350.451"}, {"rent_eur": None, "rent_min_eur": 400, "rent_max_eur": 300},
               {"rent_eur": 300, "rent_min_eur": 400}, {"available_from": "pas une date"},
               {"audience": "PUBLIC_ONLY"}, {"email": "must-not-be-stored@example.invalid"},
               {"observed_at": "2026-10-26T10:00:00"}]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_snapshot(snapshot([test_offer(**changes)]))


class SnapshotValidationTests(unittest.TestCase):
    def test_inconsistent_complete_snapshots_and_duplicated_records_are_rejected(self):
        cases = [snapshot([test_offer()], pagination_complete=False), snapshot([test_offer()], total_count=2),
                 snapshot([test_offer(), test_offer()], total_count=2), snapshot([], total_count=None),
                 snapshot([], audience_confirmed=False), snapshot([], audience="INVALID"),
                 snapshot([], schema_version=True)]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_snapshot(payload)

    def test_unknown_has_no_offers_or_count_and_authentication_is_never_assumed(self):
        self.assertEqual(parse_snapshot(snapshot([], status="UNKNOWN")).status, "UNKNOWN")
        for payload in [snapshot([test_offer()], status="UNKNOWN"), snapshot([], status="UNKNOWN", total_count=0),
                        snapshot([], audience="AUTHENTICATED", audience_confirmed=False, status="UNKNOWN"),
                        snapshot([], audience=[], status="UNKNOWN")]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_snapshot(payload)

    def test_url_scope_cannot_be_relabelled_or_include_credentials(self):
        for changes in [{"zone": "Paris"}, {"source_url": PersonalConfig().source_url + "&access_token=secret"},
                        {"cookies": []}, {"observed_at": "2099-01-01T00:00:00Z"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_snapshot(snapshot([], **changes))
        with self.assertRaises(ValueError):
            parse_snapshot(snapshot([test_offer(url="https://trouverunlogement.lescrous.fr/tools/48/accommodations/test1")]))

    def test_json_size_duplicate_keys_and_non_finite_values_fail_closed(self):
        for payload in [b" " * (MAX_IMPORT_BYTES + 1), '{"schema_version":1,"schema_version":1}', b"\xff", '{"offers":NaN}']:
            with self.subTest(payload_type=type(payload).__name__), self.assertRaises(ValueError):
                parse_snapshot(payload)


class StoreOfferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "personal.sqlite3"
        self.config = PersonalConfig()

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_and_repeated_capture_do_not_duplicate_new_offer_events(self):
        capture = parse_snapshot(snapshot([test_offer()]))
        with PersonalStore(self.path) as store:
            first = store.import_offers(self.config, capture)
            self.assertEqual(first["new"], 1)
        with PersonalStore(self.path) as store:
            same_file = store.import_offers(self.config, capture)
            same_data_later = store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], minute=1)))
            self.assertTrue(same_file["duplicate_import"])
            self.assertEqual(same_data_later["event_ids"], [])
            self.assertEqual(len(store.offer_events(self.config.profile_key, "AUTHENTICATED")), 1)
            self.assertEqual(len(store.offer_history(self.config.profile_key, "AUTHENTICATED", "crous:47:test1")), 1)

    def test_price_change_is_a_revision_with_previous_and_current_values(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer(rent_eur=350.45)])))
            result = store.import_offers(self.config, parse_snapshot(snapshot([test_offer(rent_eur=325.4)], minute=1)))
            self.assertEqual(result["updated"], 1)
            event = store.offer_events(self.config.profile_key, "AUTHENTICATED")[0]
            self.assertEqual(event["kind"], "UPDATED_MATCH")
            self.assertEqual(event["changed_fields"], ["rent_eur"])
            self.assertEqual(event["previous"]["rent_eur"], "350.45")
            self.assertEqual(event["current"]["rent_eur"], "325.40")
            self.assertEqual(len(store.offer_events(self.config.profile_key, "AUTHENTICATED", limit=None)), 2)
            self.assertEqual(len(store.offer_history(self.config.profile_key, "AUTHENTICATED", "crous:47:test1", limit=None)), 2)

    def test_partial_and_unknown_do_not_remove_existing_offers_but_complete_zero_does(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer()])))
            for minute, state in [(1, "PARTIAL"), (2, "UNKNOWN")]:
                result = store.import_offers(self.config, parse_snapshot(snapshot([], minute=minute, status=state)))
                self.assertEqual(result["removed"], 0)
                self.assertEqual(len(store.offers(self.config.profile_key, "AUTHENTICATED")), 1)
            complete = store.import_offers(self.config, parse_snapshot(snapshot([], minute=3)))
            self.assertEqual(complete["removed"], 1)
            self.assertEqual(store.offers(self.config.profile_key, "AUTHENTICATED"), [])
            self.assertFalse(store.offers(self.config.profile_key, "AUTHENTICATED", True)[0]["active"])
            self.assertEqual(store.offer_events(self.config.profile_key, "AUTHENTICATED")[0]["kind"], "REMOVED_MATCH")

    def test_public_and_authenticated_data_and_timestamps_remain_separate(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], audience="AUTHENTICATED")))
            store.import_offers(self.config, parse_snapshot(snapshot([], minute=3, audience="PUBLIC_ONLY")))
            self.assertEqual(len(store.offers(self.config.profile_key, "AUTHENTICATED")), 1)
            self.assertEqual(store.offers(self.config.profile_key, "PUBLIC_ONLY"), [])
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], minute=1, audience="PUBLIC_ONLY")))
            self.assertEqual(store.offers(self.config.profile_key, "PUBLIC_ONLY"), [])
            self.assertEqual(len(store.offer_events(self.config.profile_key, "AUTHENTICATED")), 1)

    def test_old_snapshot_cannot_erase_or_revert_a_newer_price(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer(rent_eur=300)], minute=3)))
            old = store.import_offers(self.config, parse_snapshot(snapshot([], minute=2)))
            equal_time = store.import_offers(self.config, parse_snapshot(snapshot([test_offer(rent_eur=900)], minute=3)))
            self.assertTrue(old["stale_import"])
            self.assertTrue(equal_time["stale_import"])
            self.assertEqual(store.offers(self.config.profile_key, "AUTHENTICATED")[0]["rent_eur"], "300.00")

    def test_reappearance_is_reported_once_even_when_content_is_unchanged(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer()])))
            store.import_offers(self.config, parse_snapshot(snapshot([], minute=1)))
            back = store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], minute=2)))
            self.assertEqual(back["reappeared"], 1)
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], minute=3)))
            self.assertEqual(store.offer_events(self.config.profile_key, "AUTHENTICATED")[0]["kind"], "REAPPEARED_MATCH")
            self.assertEqual(len(store.offer_events(self.config.profile_key, "AUTHENTICATED")), 3)

    def test_unknown_room_type_is_review_only_and_studio_does_not_notify(self):
        with PersonalStore(self.path) as store:
            result = store.import_offers(self.config, parse_snapshot(snapshot([test_offer(housing_type=None), test_offer("test2", housing_type="studio")])))
            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["review_required"], 1)
            events = store.offer_events(self.config.profile_key, "AUTHENTICATED")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "REVIEW_REQUIRED")
            self.assertEqual(events[0]["match_state"], "UNKNOWN")

    def test_newly_confirmed_type_does_not_remain_silent(self):
        with PersonalStore(self.path) as store:
            store.import_offers(self.config, parse_snapshot(snapshot([test_offer(housing_type=None)])))
            result = store.import_offers(self.config, parse_snapshot(snapshot([test_offer()], minute=1)))
            self.assertEqual(result["matched"], 1)
            self.assertEqual(store.offer_events(self.config.profile_key, "AUTHENTICATED")[0]["kind"], "MATCH_CONFIRMED")

    def test_changed_search_scope_and_untyped_input_are_rejected(self):
        changed = snapshot([], source_url=PersonalConfig().source_url + "&price=300")
        with PersonalStore(self.path) as store:
            with self.assertRaises(ValueError):
                store.import_offers(self.config, parse_snapshot(changed))
            with self.assertRaises(ValueError):
                store.import_offers(self.config, snapshot([]))
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM offer_snapshots").fetchone()[0], 0)

    def test_import_failure_rolls_back_snapshots_offers_revisions_and_events(self):
        with PersonalStore(self.path) as store:
            store.db.executescript("CREATE TRIGGER fail_offer_event BEFORE INSERT ON offer_events BEGIN SELECT RAISE(ABORT,'test only'); END;")
            with self.assertRaises(sqlite3.IntegrityError):
                store.import_offers(self.config, parse_snapshot(snapshot([test_offer()])))
            for table in ("offer_snapshots", "tracked_offers", "offer_revisions", "offer_events", "offer_scopes", "profiles"):
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)


class MigrationTests(unittest.TestCase):
    def test_v1_migration_preserves_three_histories_notes_notices_and_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE profiles(profile_key TEXT PRIMARY KEY,config_json TEXT NOT NULL,last_signature TEXT,revision INTEGER NOT NULL DEFAULT 0,last_valid_check_id INTEGER);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY AUTOINCREMENT,profile_key TEXT NOT NULL,checked_at TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,public_count INTEGER,evidence TEXT NOT NULL,source_url TEXT NOT NULL,fingerprint TEXT NOT NULL,http_status INTEGER,audience TEXT NOT NULL);
                    CREATE TABLE notices(id INTEGER PRIMARY KEY AUTOINCREMENT,profile_key TEXT NOT NULL,check_id INTEGER NOT NULL,revision INTEGER NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,read_at TEXT,UNIQUE(profile_key,revision));
                    CREATE TABLE watchlist(key TEXT PRIMARY KEY,url TEXT UNIQUE NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                    INSERT INTO settings VALUES('network_next_allowed_at','2000');
                    INSERT INTO profiles VALUES('legacy','{}','old-signature',3,1);
                    INSERT INTO notices VALUES(1,'legacy',1,1,'REVIEW_REQUIRED','Message personnel à conserver','2026-10-26',NULL);
                    INSERT INTO watchlist VALUES('legacy-watch','https://trouverunlogement.lescrous.fr/','Suivi personnel','interested','Note à conserver','2026-10-26','2026-10-26');
                    PRAGMA user_version=1;
                """)
                for number in range(3):
                    db.execute("INSERT INTO checks(profile_key,checked_at,state,reason,public_count,evidence,source_url,fingerprint,http_status,audience) VALUES('legacy',?,'NO_PUBLIC_RESULTS','OLD',0,'Preuve','https://trouverunlogement.lescrous.fr/','f',200,'PUBLIC_ONLY')", (f"2026-10-26T10:0{number}:00Z",))
                db.commit()
            with PersonalStore(path) as store:
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(len(store.history("legacy")), 3)
                self.assertEqual(store.watches()[0]["note"], "Note à conserver")
                self.assertEqual(store.notices("legacy")[0]["message"], "Message personnel à conserver")
                self.assertEqual(store.claim_check(now=1500), 500)
                store.import_offers(PersonalConfig(), parse_snapshot(snapshot([test_offer()])))
            with PersonalStore(path) as reopened:
                self.assertEqual(len(reopened.history("legacy")), 3)
                self.assertEqual(len(reopened.offers(PersonalConfig().profile_key, "AUTHENTICATED")), 1)
