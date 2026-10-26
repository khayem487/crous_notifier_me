from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crous_personal.__main__ import main, perform_check
from crous_personal.config import PersonalConfig, load_personal_config
from crous_personal.source import Observation, analyze_public_page, check_public_source
from crous_personal.store import PersonalStore


class ConfigTests(unittest.TestCase):
    def test_defaults_are_cergy_room_without_an_invented_budget(self):
        config = PersonalConfig()
        self.assertEqual(config.zone, "Cergy")
        self.assertEqual(config.housing_type, "chambre")
        self.assertIsNone(config.max_rent_eur)
        self.assertIn("locationName=Cergy", config.source_url)
        self.assertEqual(config.profile_key, replace(config, database="elsewhere.sqlite3", interval_seconds=600).profile_key)
        self.assertNotEqual(config.profile_key, replace(config, max_rent_eur="400").profile_key)

    def test_invalid_configuration_is_not_silently_accepted(self):
        for values in [{"zone": ""}, {"zone": "Cergy\nParis"}, {"max_rent_eur": "NaN"},
                       {"max_rent_eur": -1}, {"interval_seconds": 1}, {"interval_seconds": 120}, {"interval_seconds": True},
                       {"request_timeout_seconds": 60}, {"source_url": "https://localhost/"}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                PersonalConfig(**values)


class VerifiedSourceTests(unittest.TestCase):
    def test_observed_official_zero_is_public_only_and_not_a_claim_about_dse_rooms(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/cergy-public-zero-oct26.json").read_text(encoding="utf-8"))
        result = analyze_public_page(fixture["text"], PersonalConfig(), fixture["http_status"])
        self.assertEqual(result.state, "NO_PUBLIC_RESULTS")
        self.assertEqual(result.public_count, 0)
        self.assertEqual(result.audience, "PUBLIC_ONLY")
        self.assertFalse(result.rooms_confirmed)
        self.assertIn("DSE", result.as_dict()["limitation"])

    def test_unverified_positive_structure_never_becomes_an_available_room(self):
        for text in ["3 logements trouvés pour Cergy", "Des chambres sont disponibles à Cergy", "Bienvenue"]:
            with self.subTest(text=text):
                result = analyze_public_page(text, PersonalConfig())
                self.assertEqual(result.state, "UNKNOWN")
                self.assertIsNone(result.public_count)
                self.assertFalse(result.rooms_confirmed)

    def test_wrong_zone_or_conflicting_results_are_unknown(self):
        wrong = replace(PersonalConfig(), zone="Paris")
        self.assertEqual(analyze_public_page("Aucun logement trouvé pour Paris", wrong).state, "UNKNOWN")
        self.assertEqual(analyze_public_page("Aucun logement trouvé pour Cergy. 3 logements disponibles", PersonalConfig()).state, "UNKNOWN")

    @patch("crous_personal.source._fetch_bounded_page_result", side_effect=TimeoutError)
    def test_source_error_does_not_claim_no_rooms(self, fetch):
        observation = check_public_source(PersonalConfig())
        self.assertEqual(observation.state, "UNKNOWN")
        self.assertIsNone(observation.public_count)
        self.assertEqual(observation.reason, "SOURCE_UNREACHABLE")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "personal.sqlite3"
        self.config = PersonalConfig()
        self.zero = analyze_public_page("Aucun logement trouvé pour Cergy", self.config)
        self.unknown = analyze_public_page("Format changé pour Cergy", self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_keeps_history_and_deduplicates_repeated_source_notices(self):
        with PersonalStore(self.path) as store:
            first = store.record(self.config, self.unknown)
            self.assertIsNotNone(first["new_notice_id"])
        with PersonalStore(self.path) as store:
            second = store.record(self.config, self.unknown)
            self.assertFalse(second["changed"])
            self.assertIsNone(second["new_notice_id"])
            self.assertEqual(len(store.history(self.config.profile_key)), 2)
            self.assertEqual(len(store.notices(self.config.profile_key)), 1)

    def test_unknown_keeps_the_last_reliable_public_observation(self):
        with PersonalStore(self.path) as store:
            good = store.record(self.config, self.zero)
            store.record(self.config, self.unknown)
            status = store.status(self.config.profile_key)
            self.assertEqual(status["last_check"]["state"], "UNKNOWN")
            self.assertEqual(status["last_reliable_public_check"]["id"], good["check_id"])
            self.assertEqual(status["last_reliable_public_check"]["public_count"], 0)

    def test_empty_public_result_does_not_generate_a_room_alert(self):
        with PersonalStore(self.path) as store:
            result = store.record(self.config, self.zero)
            self.assertIsNone(result["new_notice_id"])
            self.assertEqual(store.notices(self.config.profile_key), [])

    def test_cooldown_survives_restart_and_is_shared_between_connections(self):
        with PersonalStore(self.path) as first, PersonalStore(self.path) as second:
            self.assertEqual(first.claim_check(now=1000), 0)
            self.assertEqual(second.claim_check(now=1000), 300)
        with PersonalStore(self.path) as reopened:
            self.assertEqual(reopened.claim_check(now=1299), 1)
            self.assertEqual(reopened.claim_check(now=1300), 0)

    def test_search_preferences_have_separate_histories(self):
        other = replace(self.config, max_rent_eur="400")
        with PersonalStore(self.path) as store:
            store.record(self.config, self.zero)
            self.assertIsNone(store.status(other.profile_key)["last_check"])

    def test_recovered_then_changed_source_can_generate_a_new_local_notice(self):
        with PersonalStore(self.path) as store:
            a = store.record(self.config, self.unknown)
            store.record(self.config, self.zero)
            b = store.record(self.config, self.unknown)
            self.assertNotEqual(a["new_notice_id"], b["new_notice_id"])
            self.assertEqual(len(store.notices(self.config.profile_key)), 2)

    def test_zero_disappearing_creates_a_deduplicated_review_notice_without_claiming_rooms(self):
        unverified_results = analyze_public_page("3 logements trouvés pour Cergy", self.config)
        with PersonalStore(self.path) as store:
            known = store.record(self.config, self.zero)
            changed = store.record(self.config, unverified_results)
            notice = store.notices(self.config.profile_key)[0]
            self.assertEqual(notice["kind"], "REVIEW_REQUIRED")
            self.assertIsNotNone(changed["new_notice_id"])
            self.assertEqual(changed["observation"]["state"], "UNKNOWN")
            self.assertIsNone(changed["observation"]["public_count"])
            self.assertFalse(changed["observation"]["rooms_confirmed"])
        with PersonalStore(self.path) as store:
            repeated = store.record(self.config, unverified_results)
            self.assertIsNone(repeated["new_notice_id"])
            self.assertEqual(len(store.notices(self.config.profile_key)), 1)
            self.assertEqual(store.status(self.config.profile_key)["last_reliable_public_check"]["id"], known["check_id"])

    def test_sso_or_token_links_cannot_enter_the_watchlist(self):
        with PersonalStore(self.path) as store:
            for query in ["ticket=not-real", "state=not-real", "SAMLResponse=not-real", "session_id=not-real", "access_token=not-real"]:
                with self.subTest(query=query), self.assertRaises(ValueError):
                    store.add_watch("https://trouverunlogement.lescrous.fr/?" + query, "Lien à refuser")
            self.assertEqual(store.watches(True), [])

    def test_watchlist_is_persistent_and_adding_a_duplicate_preserves_personal_notes(self):
        with PersonalStore(self.path) as store:
            watch = store.add_watch(self.config.source_url, "Recherche Cergy", "Mes notes")
            store.update_watch(watch["key"][:12], "applied", "Démarche enregistrée manuellement")
        with PersonalStore(self.path) as store:
            duplicate = store.add_watch(self.config.source_url, "Titre à ne pas écraser", "À ne pas écraser")
            self.assertEqual(duplicate["status"], "applied")
            self.assertEqual(duplicate["note"], "Démarche enregistrée manuellement")
            self.assertEqual(len(store.watches(True)), 1)

    def test_acknowledgement_is_profile_scoped_and_persistent(self):
        with PersonalStore(self.path) as store:
            notice = store.record(self.config, self.unknown)["new_notice_id"]
            self.assertEqual(store.acknowledge("another-profile", [notice]), 0)
            self.assertEqual(store.acknowledge(self.config.profile_key, [notice]), 1)
        with PersonalStore(self.path) as store:
            self.assertEqual(store.notices(self.config.profile_key), [])
            self.assertEqual(len(store.notices(self.config.profile_key, True)), 1)

    def test_failed_transaction_does_not_leave_a_partial_history_or_profile(self):
        with PersonalStore(self.path) as store:
            store.db.executescript("CREATE TRIGGER deny_notice BEFORE INSERT ON notices BEGIN SELECT RAISE(ABORT,'test only'); END;")
            with self.assertRaises(sqlite3.IntegrityError):
                store.record(self.config, self.unknown)
            self.assertEqual(store.history(self.config.profile_key), [])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 0)

    def test_newer_schema_is_not_downgraded(self):
        with PersonalStore(self.path) as store:
            store.db.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            PersonalStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_cooldown_skips_the_checker_without_a_network_attempt(self):
        with PersonalStore(self.path) as store:
            with patch("crous_personal.__main__.check_public_source", return_value=self.zero) as checker:
                first = perform_check(self.config, store)
                second = perform_check(self.config, store)
            self.assertEqual(first["observation"]["state"], "NO_PUBLIC_RESULTS")
            self.assertTrue(second["skipped"])
            self.assertEqual(checker.call_count, 1)

    def test_sqlite_backup_restores_history_and_notes_while_wal_database_is_open(self):
        destination = Path(self.tmp.name) / "backup.sqlite3"
        with PersonalStore(self.path) as store:
            store.record(self.config, self.zero)
            store.add_watch(self.config.source_url, "Recherche", "Note personnelle")
            store.backup(destination)
            with self.assertRaises(ValueError):
                store.backup(self.path)
            with self.assertRaises(ValueError):
                store.backup(destination)
        with PersonalStore(destination) as restored:
            self.assertEqual(len(restored.history(self.config.profile_key)), 1)
            self.assertEqual(restored.watches()[0]["note"], "Note personnelle")


class CliTests(unittest.TestCase):
    def test_init_and_read_commands_never_start_monitoring_or_external_notifications(self):
        with tempfile.TemporaryDirectory() as directory, patch("crous_personal.__main__.check_public_source") as fetch, \
                patch("crous_notifier.send_telegram") as send:
            config_path = Path(directory) / "cergy.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["--config", str(config_path), "init"]), 0)
                config = load_personal_config(config_path)
                self.assertEqual(config.zone, "Cergy")
                self.assertIsNone(config.max_rent_eur)
                self.assertEqual(main(["--config", str(config_path), "status"]), 0)
                self.assertEqual(main(["--config", str(config_path), "history"]), 0)
                self.assertEqual(main(["--config", str(config_path), "watch", "list"]), 0)
            original = config_path.read_bytes()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(["--config", str(config_path), "init"]), 2)
            self.assertEqual(config_path.read_bytes(), original)
            fetch.assert_not_called()
            send.assert_not_called()
