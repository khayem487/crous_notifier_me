import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import app as web
from crous_notifier import OFFICIAL_URL, analyze_text, detect_state, fetch_page_result, fetch_page_text, run_once, validate_source_url
from crous_search import CrousSearch, analyze_search_text, fetch_search_page_result, validate_search_url

# Synthetic fixture only; no claim that this path/query is implemented by CROUS.
SEARCH_URL = "https://trouverunlogement.lescrous.fr/configured-search?area=Cergy&max_rent=400"


class ParserTests(unittest.TestCase):
    def test_navigation_words_do_not_report_a_housing_offer(self):
        for text in ["Nos offres", "Soyez informé de la disponibilité", "Logements disponibles",
                     "Offres indisponibles et disponibles : aide", "Le service est indisponible", "Bienvenue",
                     "Recevoir une alerte quand 3 logements disponibles", "Exemple : 12 logements disponibles"]:
            with self.subTest(text=text):
                self.assertEqual(detect_state(text), "UNKNOWN")

    def test_explicit_counts_and_no_results(self):
        self.assertEqual(analyze_text("Résultats : 12 logements disponibles")["count"], 12)
        self.assertEqual(detect_state("3 offres trouvées"), "AVAILABLE")
        for text in ["Aucune offre disponible pour cette recherche", "Aucun logement", "0 logements disponibles"]:
            self.assertEqual(detect_state(text), "UNAVAILABLE")

    def test_conflicting_results_are_not_presented_as_available(self):
        self.assertEqual(detect_state("3 offres disponibles. Aucune offre"), "UNKNOWN")
        self.assertEqual(detect_state("3 offres disponibles. 12 logements disponibles"), "UNKNOWN")


class FetchSafetyTests(unittest.TestCase):
    @patch("crous_notifier.fetch_page_result")
    @patch("crous_notifier.requests.get")
    def test_cli_keeps_its_configured_search_url_and_query(self, get, public_fetch):
        configured = "https://trouverunlogement.lescrous.fr/tools/36/search?location=Paris&price=400"
        get.return_value.text = "<main>3 logements disponibles</main>"
        text = fetch_page_text(configured, 25)
        self.assertEqual(get.call_args.args[0], configured)
        self.assertEqual(get.call_args.kwargs["timeout"], 25)
        self.assertEqual(detect_state(text), "AVAILABLE")
        public_fetch.assert_not_called()

    def test_only_the_fixed_official_root_is_accepted(self):
        self.assertEqual(validate_source_url(OFFICIAL_URL), OFFICIAL_URL)
        for url in ["http://trouverunlogement.lescrous.fr/", "https://127.0.0.1/", "https://169.254.169.254/",
                    "https://trouverunlogement.lescrous.fr.evil.example/", "https://trouverunlogement.lescrous.fr@localhost/",
                    "https://trouverunlogement.lescrous.fr:443/", "https://trouverunlogement.lescrous.fr/?url=http://localhost/",
                    "file:///etc/passwd", "https://trouverunlogement.lescrous.fr/private"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source_url(url)

    @patch("crous_notifier.requests.Session")
    def test_redirects_are_not_followed(self, session_factory):
        client = session_factory.return_value.__enter__.return_value
        response = client.get.return_value.__enter__.return_value
        response.status_code = 302
        with self.assertRaises(ValueError):
            fetch_page_result()
        self.assertFalse(client.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.args[0], OFFICIAL_URL)
        self.assertFalse(client.trust_env)

    @patch("crous_notifier.requests.Session")
    def test_hidden_and_navigation_text_are_ignored(self, session_factory):
        client = session_factory.return_value.__enter__.return_value
        response = client.get.return_value.__enter__.return_value
        response.status_code = 200
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.iter_content.return_value = [b'<html><nav>99 logements disponibles</nav><script>88 logements disponibles</script><main>Aucune offre</main></html>']
        self.assertEqual(detect_state(fetch_page_result()["text"]), "UNAVAILABLE")


class WebTests(unittest.TestCase):
    def setUp(self):
        web._cache.clear()
        web._next_fetch = 0
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()

    def test_page_is_real_and_does_not_start_a_background_check(self):
        with patch("app.fetch_page_result") as fetch:
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Aucune vérification lancée", response.get_data(as_text=True))
            for field in ["source-url", "search-zone", "search-criteria", "search-form"]:
                self.assertIn(f'id="{field}"', response.get_data(as_text=True))
            fetch.assert_not_called()
        health = self.client.get("/health").get_json()
        self.assertFalse(health["notifications_enabled"])
        self.assertFalse(health["background_monitoring"])

    def test_query_parameters_cannot_turn_the_checker_into_a_proxy(self):
        with patch("app.fetch_page_result") as fetch:
            self.assertEqual(self.client.get("/api/check?url=http://127.0.0.1/").status_code, 400)
            self.assertEqual(self.client.get("/api/check?source=arbitrary").status_code, 400)
            fetch.assert_not_called()

    def test_real_parser_result_is_cached_and_never_sends_notifications(self):
        with patch("app.fetch_page_result", return_value={"text": "7 logements disponibles", "http_status": 200}) as fetch, \
                patch("crous_notifier.send_telegram") as telegram, patch("crous_notifier.load_config") as config, \
                patch("crous_notifier.save_last_state") as save:
            first = self.client.get("/api/check?source=official").get_json()
            second = self.client.get("/api/check?source=official").get_json()
            self.assertEqual(first["state"], "AVAILABLE")
            self.assertEqual(first["count"], 7)
            self.assertEqual(first["checked_at"], second["checked_at"])
            self.assertEqual(fetch.call_count, 1)
            telegram.assert_not_called()
            config.assert_not_called()
            save.assert_not_called()

    def test_unreachable_source_is_unknown_not_no_housing(self):
        with patch("app.fetch_page_result", side_effect=TimeoutError):
            result = self.client.get("/api/check").get_json()
            self.assertEqual(result["state"], "UNKNOWN")
            self.assertEqual(result["reason"], "SOURCE_UNREACHABLE")
            self.assertIsNone(result["count"])
            self.assertFalse(result["fetch_ok"])


class SearchConfigurationTests(unittest.TestCase):
    def test_official_link_preserves_its_copied_path_and_criteria(self):
        self.assertEqual(validate_search_url(SEARCH_URL), SEARCH_URL)
        self.assertEqual(validate_search_url("https://trouverunlogement.lescrous.fr"), OFFICIAL_URL)
        search = CrousSearch.from_payload({"source_url": SEARCH_URL, "zone": " Cergy ", "criteria": "Loyer maximum : 400 €"})
        self.assertEqual(search.zone, "Cergy")
        self.assertEqual(search.source_url, SEARCH_URL)

    def test_links_cannot_change_host_or_embed_private_credentials(self):
        for url in ["http://trouverunlogement.lescrous.fr/", "https://localhost/", "https://127.0.0.1/",
                    "https://trouverunlogement.lescrous.fr.evil.example/", "https://trouverunlogement.lescrous.fr@localhost/",
                    "https://user:password@trouverunlogement.lescrous.fr/", "https://trouverunlogement.lescrous.fr:443/",
                    OFFICIAL_URL + "#search", OFFICIAL_URL + "?access_token=not-real", OFFICIAL_URL + "?%74oken=not-real",
                    OFFICIAL_URL + "?session=not-real", OFFICIAL_URL + "%0dheaders", OFFICIAL_URL + "path%broken",
                    OFFICIAL_URL + "\n", OFFICIAL_URL + "x" * 2048]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_search_url(url)

    def test_search_requires_a_real_link_and_bounded_descriptions(self):
        for payload in [{"source_url": SEARCH_URL}, {"source_url": OFFICIAL_URL, "zone": "Cergy"},
                        {"source_url": SEARCH_URL, "zone": ["Cergy"]}, {"source_url": SEARCH_URL, "zone": "C" * 101},
                        {"source_url": SEARCH_URL, "zone": "Cergy", "criteria": "x" * 301},
                        {"source_url": SEARCH_URL, "zone": "Cergy\nParis"}, {"url": SEARCH_URL}, [], None]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                CrousSearch.from_payload(payload)

    def test_count_is_unknown_until_zone_and_criteria_are_confirmed_in_text(self):
        search = CrousSearch(SEARCH_URL, "Cergy", "Loyer maximum : 400 €")
        for text in ["3 logements disponibles", "Paris. Loyer maximum : 400 €. 3 logements disponibles",
                     "Cergy. 3 logements disponibles", "Cergy. Loyer maximum : 4000 €. 3 logements disponibles"]:
            with self.subTest(text=text):
                result = analyze_search_text(text, search)
                self.assertEqual(result["state"], "UNKNOWN")
                self.assertIsNone(result["count"])
                self.assertEqual(result["reason"], "SEARCH_SCOPE_NOT_CONFIRMED")
        known = analyze_search_text("Recherche Cergy. Loyer maximum : 400 €. Résultats : 3 logements disponibles", search)
        self.assertEqual(known["state"], "AVAILABLE")
        self.assertEqual(known["count"], 3)
        self.assertTrue(known["zone_confirmed_in_text"])
        self.assertTrue(known["criteria_confirmed_in_text"])
        self.assertEqual(analyze_search_text("Cergy. Loyer maximum : 400 €. Aucune offre", search)["state"], "UNAVAILABLE")

    @patch("crous_search._fetch_bounded_page_result", return_value={"text": "Cergy. 3 logements disponibles", "http_status": 200})
    def test_configured_fetch_uses_the_same_bounded_transport(self, fetch):
        search = CrousSearch(SEARCH_URL, "Cergy")
        result = fetch_search_page_result(search, timeout=10)
        self.assertEqual(result["http_status"], 200)
        fetch.assert_called_once_with(SEARCH_URL, 10)


class SearchApiTests(unittest.TestCase):
    def setUp(self):
        web._cache.clear()
        web._next_fetch = 0
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.search = {"source_url": SEARCH_URL, "zone": "Cergy", "criteria": "Loyer maximum : 400 €"}

    @patch("app.fetch_search_page_result", return_value={"text": "Recherche Cergy. Loyer maximum : 400 €. 3 logements disponibles", "http_status": 200})
    def test_form_search_reaches_real_parser_without_any_notification(self, fetch):
        with patch("crous_notifier.send_telegram") as send, patch("crous_notifier.load_config") as config, patch("crous_notifier.save_last_state") as save:
            response = self.client.post("/api/check", json=self.search)
        result = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["state"], "AVAILABLE")
        self.assertEqual(result["search"], self.search)
        self.assertEqual(result["check_type"], "configured-public-search")
        self.assertFalse(result["notifications_enabled"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(fetch.call_args.args[0].source_url, SEARCH_URL)
        send.assert_not_called()
        config.assert_not_called()
        save.assert_not_called()

    @patch("app.fetch_search_page_result")
    @patch("app.fetch_page_result")
    def test_invalid_or_oversized_configuration_is_rejected_without_network(self, root_fetch, search_fetch):
        for payload in [{**self.search, "source_url": "https://127.0.0.1/"}, {**self.search, "zone": ""},
                        {**self.search, "token": "not-real"}, {**self.search, "source_url": OFFICIAL_URL}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/check", json=payload).status_code, 400)
        self.assertEqual(self.client.post("/api/check?url=https://localhost/", json=self.search).status_code, 400)
        self.assertEqual(self.client.post("/api/check", data="x" * 9000, content_type="application/json").status_code, 413)
        root_fetch.assert_not_called()
        search_fetch.assert_not_called()

    @patch("app.fetch_search_page_result", return_value={"text": "Cergy. Loyer maximum : 400 €. 3 logements disponibles", "http_status": 200})
    def test_same_source_reuses_page_but_rechecks_each_search_scope(self, fetch):
        first = self.client.post("/api/check", json=self.search).get_json()
        second = self.client.post("/api/check", json={**self.search, "zone": "Paris"}).get_json()
        self.assertEqual(first["state"], "AVAILABLE")
        self.assertEqual(second["state"], "UNKNOWN")
        self.assertEqual(second["search"]["zone"], "Paris")
        self.assertEqual(first["checked_at"], second["checked_at"])
        self.assertEqual(fetch.call_count, 1)

    @patch("app.fetch_search_page_result", return_value={"text": "Cergy. 3 logements disponibles", "http_status": 200})
    def test_changing_the_url_cannot_bypass_the_instance_cooldown(self, fetch):
        self.assertEqual(self.client.post("/api/check", json=self.search).status_code, 200)
        second = self.client.post("/api/check", json={**self.search, "source_url": SEARCH_URL + "&other=1"})
        self.assertEqual(second.status_code, 429)
        self.assertGreater(int(second.headers["Retry-After"]), 0)
        self.assertEqual(fetch.call_count, 1)


class NotificationReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"url": OFFICIAL_URL, "timeout": 10,
                    "telegram_token": "test-only-token", "telegram_chat_id": "test-only-chat"}

    @patch("crous_notifier.save_last_state")
    @patch("crous_notifier.send_telegram")
    @patch("crous_notifier.load_last_state", return_value="AVAILABLE")
    @patch("crous_notifier.fetch_page_text", return_value="Bienvenue sur le portail")
    def test_unknown_page_keeps_last_reliable_state_without_notification(self, fetch, load, send, save):
        with redirect_stdout(StringIO()):
            self.assertEqual(run_once(self.cfg), 1)
        load.assert_not_called()
        send.assert_not_called()
        save.assert_not_called()

    @patch("crous_notifier.save_last_state")
    @patch("crous_notifier.send_telegram")
    @patch("crous_notifier.load_last_state", return_value="AVAILABLE")
    @patch("crous_notifier.fetch_page_text", return_value="3 logements disponibles")
    def test_unchanged_reliable_result_does_not_repeat_an_alert(self, fetch, load, send, save):
        with redirect_stdout(StringIO()):
            self.assertEqual(run_once(self.cfg), 0)
        send.assert_not_called()
        save.assert_not_called()

    @patch("crous_notifier.save_last_state")
    @patch("crous_notifier.send_telegram", side_effect=[RuntimeError("https://api.telegram.org/bottest-only-token/sendMessage"), True])
    @patch("crous_notifier.load_last_state", return_value="UNAVAILABLE")
    @patch("crous_notifier.fetch_page_text", return_value="3 logements disponibles")
    def test_failed_alert_is_retried_without_logging_the_token(self, fetch, load, send, save):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_once(self.cfg), 1)
            save.assert_not_called()
            self.assertEqual(run_once(self.cfg), 0)
        self.assertNotIn(self.cfg["telegram_token"], output.getvalue())
        self.assertEqual(send.call_count, 2)
        save.assert_called_once_with("AVAILABLE")

    @patch("crous_notifier.save_last_state")
    @patch("crous_notifier.send_telegram", return_value=False)
    @patch("crous_notifier.load_last_state", return_value="UNAVAILABLE")
    @patch("crous_notifier.fetch_page_text", return_value="3 logements disponibles")
    def test_false_delivery_result_is_not_acknowledged(self, fetch, load, send, save):
        with redirect_stdout(StringIO()):
            self.assertEqual(run_once(self.cfg), 1)
        save.assert_not_called()

    @patch("crous_notifier.save_last_state")
    @patch("crous_notifier.send_telegram")
    @patch("crous_notifier.load_last_state", return_value="UNAVAILABLE")
    @patch("crous_notifier.fetch_page_text", return_value="3 logements disponibles")
    def test_monitor_without_telegram_records_known_state_without_network_delivery(self, fetch, load, send, save):
        with redirect_stdout(StringIO()):
            self.assertEqual(run_once({**self.cfg, "telegram_token": "", "telegram_chat_id": ""}), 0)
        send.assert_not_called()
        save.assert_called_once_with("AVAILABLE")


if __name__ == "__main__":
    unittest.main()
