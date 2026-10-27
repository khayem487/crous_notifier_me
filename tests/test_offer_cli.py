"""Offline CLI boundary checks; no real offer or authenticated session is used."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crous_personal.__main__ import MAX_SNAPSHOT_BYTES, main
from crous_personal.config import CERGY_PUBLIC_URL


class OfferCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "test.personal.json"
        self.incoming = self.root / "snapshot.json"
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("CLI import must stay offline"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.assertEqual(self.invoke("init")[0], 0)

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(["--config", str(self.config), "--json", *args])
        return code, output.getvalue(), error.getvalue()

    def snapshot(self):
        return {
            "schema_version": 1, "source_url": CERGY_PUBLIC_URL,
            "zone": "Cergy", "audience": "PUBLIC_ONLY", "audience_confirmed": True,
            "observed_at": "2026-10-26T19:55:35+00:00", "status": "COMPLETE",
            "pagination_complete": True, "total_count": 0, "offers": [],
        }

    def test_import_and_restart_queries_are_offline_and_deduplicated(self):
        self.incoming.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        code, output, error = self.invoke("import-offers", str(self.incoming))
        self.assertEqual((code, error), (0, ""))
        self.assertFalse(json.loads(output)["duplicate_import"])
        code, output, error = self.invoke("import-offers", str(self.incoming))
        self.assertEqual((code, error), (0, ""))
        self.assertTrue(json.loads(output)["duplicate_import"])
        for command in ("offers", "offer-events"):
            code, output, error = self.invoke(command, "--audience", "PUBLIC_ONLY")
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output), [])
        code, output, error = self.invoke("export")
        self.assertEqual((code, error), (0, ""))
        exported = json.loads(output)
        self.assertEqual(exported["offers_by_audience"], {"PUBLIC_ONLY": [], "AUTHENTICATED": []})
        self.assertEqual(exported["offer_events_by_audience"], {"PUBLIC_ONLY": [], "AUTHENTICATED": []})

    def test_oversized_file_is_rejected_without_import(self):
        self.incoming.write_bytes(b" " * (MAX_SNAPSHOT_BYTES + 1))
        code, output, error = self.invoke("import-offers", str(self.incoming))
        self.assertEqual(code, 2)
        self.assertIn("2 Mo", error)
        self.assertEqual(output, "")
        self.assertEqual(json.loads(self.invoke("offers")[1]), [])

    def test_credential_fields_are_rejected_and_not_echoed(self):
        payload = {**self.snapshot(), "cookie": "DUMMY-NEVER-A-REAL-SECRET"}
        self.incoming.write_text(json.dumps(payload), encoding="utf-8")
        code, output, error = self.invoke("import-offers", str(self.incoming))
        self.assertEqual(code, 2)
        self.assertNotIn(payload["cookie"], output + error)
        self.assertEqual(json.loads(self.invoke("offers")[1]), [])

    def test_unrepresentable_utc_date_is_rejected_without_traceback_or_import(self):
        payload = {**self.snapshot(), "observed_at": "0001-01-01T00:00:00+23:59"}
        self.incoming.write_text(json.dumps(payload), encoding="utf-8")
        code, output, error = self.invoke("import-offers", str(self.incoming))
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("Horodatage", error)
        self.assertNotIn("Traceback", error)
        self.assertEqual(json.loads(self.invoke("offers")[1]), [])


if __name__ == "__main__":
    unittest.main()
