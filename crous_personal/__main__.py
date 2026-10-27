import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

from crous_personal.config import PersonalConfig, load_personal_config
from crous_personal.source import PUBLIC_LIMIT, check_public_source
from crous_personal.store import PersonalStore

MAX_SNAPSHOT_BYTES = 2_000_000
AUDIENCES = ("AUTHENTICATED", "PUBLIC_ONLY")


def read_offer_snapshot(path):
    from crous_personal.importers import parse_snapshot

    with Path(path).open("rb") as incoming:
        content = incoming.read(MAX_SNAPSHOT_BYTES + 1)
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Le fichier d'annonces dépasse la limite de 2 Mo.")
    return parse_snapshot(content)


def perform_check(config, store, checker=None):
    retry = store.claim_check()
    if retry:
        return {"skipped": True, "reason": "LOCAL_COOLDOWN", "retry_after_seconds": retry,
                "status": store.status(config.profile_key), "limitation": PUBLIC_LIMIT}
    return store.record(config, (checker or check_public_source)(config))


def print_result(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list):
        if not value:
            print("Aucun élément.")
        for row in value:
            print(json.dumps(row, ensure_ascii=False))
        return
    if "observation" in value:
        observation = value["observation"]
        print(f"{observation['checked_at']} — {observation['state']} — {observation['reason']}")
        print(observation["evidence"])
        print(observation["limitation"])
        print("Source : " + observation["source_url"])
        if value["new_notice_id"]:
            print(f"À vérifier : notification locale #{value['new_notice_id']} (aucun message externe envoyé).")
        elif not value["changed"]:
            print("État identique : aucune notification locale supplémentaire.")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Suivi personnel local CROUS Cergy ; aucune alerte externe activée.")
    parser.add_argument("--config", type=Path, default=Path("cergy.json"), help="Configuration locale JSON (défaut cergy.json)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Créer une configuration Cergy/chambre sans budget maximum")
    commands.add_parser("check", help="Un seul contrôle public borné, avec historique SQLite")
    commands.add_parser("status", help="Dernier contrôle et dernier résultat public fiable")
    history = commands.add_parser("history", help="Historique local sans requête réseau")
    history.add_argument("--limit", type=int, default=20)
    notices = commands.add_parser("changes", help="Notifications locales dédupliquées, sans envoi")
    notices.add_argument("--all", action="store_true", help="Inclure les notifications déjà lues")
    ack = commands.add_parser("ack", help="Marquer des notifications locales comme lues")
    ack.add_argument("ids", nargs="+", type=int)
    watch = commands.add_parser("watch", help="Suivi manuel de liens officiels, sans candidature automatique")
    actions = watch.add_subparsers(dest="watch_command", required=True)
    add = actions.add_parser("add")
    add.add_argument("url")
    add.add_argument("--title", required=True)
    add.add_argument("--note", default="")
    listing = actions.add_parser("list")
    listing.add_argument("--all", action="store_true")
    update = actions.add_parser("update")
    update.add_argument("key")
    update.add_argument("--status", choices=["watching", "interested", "applied", "dismissed"])
    update.add_argument("--note")
    commands.add_parser("export", help="Exporter l'historique et les suivis locaux en JSON sur stdout")
    backup = commands.add_parser("backup", help="Sauvegarde SQLite cohérente, y compris en mode WAL")
    backup.add_argument("--output", type=Path, help="Nouveau fichier .sqlite3 ; défaut dans exports/")
    imported = commands.add_parser("import-offers", help="Importer un relevé JSON du compagnon Edge, sans requête réseau")
    imported.add_argument("file", type=Path)
    offers = commands.add_parser("offers", help="Annonces conservées, séparées entre profil connecté et recherche publique")
    offers.add_argument("--audience", choices=AUDIENCES, default="AUTHENTICATED")
    offers.add_argument("--all", action="store_true", help="Inclure les annonces inactives")
    events = commands.add_parser("offer-events", help="Nouvelles annonces et changements conservés localement")
    events.add_argument("--audience", choices=AUDIENCES, default="AUTHENTICATED")
    events.add_argument("--limit", type=int, default=100)
    offer_history = commands.add_parser("offer-history", help="Historique d'une annonce, sans requête réseau")
    offer_history.add_argument("offer_key")
    offer_history.add_argument("--audience", choices=AUDIENCES, default="AUTHENTICATED")
    offer_history.add_argument("--limit", type=int, default=100)
    commands.add_parser("monitor", help="Boucle locale au premier plan, seulement si lancée explicitement ; Ctrl+C arrête")
    return parser


def main(argv=None):
    # Windows pipes otherwise inherit a legacy code page and corrupt JSON accents.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            config = PersonalConfig()
            args.config.parent.mkdir(parents=True, exist_ok=True)
            with args.config.open("x", encoding="utf-8") as output:
                json.dump(config.as_dict(), output, ensure_ascii=False, indent=2)
                output.write("\n")
            print_result({"created": str(args.config), "preferences": config.as_dict(),
                          "monitoring_started": False, "notifications_enabled": False}, args.json)
            return 0
        config = load_personal_config(args.config)
        database_path = Path(config.database)
        if not database_path.is_absolute():
            database_path = args.config.resolve().parent / database_path
        with PersonalStore(database_path) as store:
            if args.command == "check":
                value = perform_check(config, store)
                print_result(value, args.json)
                return 1 if value.get("observation", {}).get("state") == "UNKNOWN" else 0
            if args.command == "status":
                print_result({"preferences": config.as_dict(), **store.status(config.profile_key),
                              "limitation": PUBLIC_LIMIT, "notifications_enabled": False,
                              "source_mode": "PUBLIC_HTTP",
                              "browser_companion_required_for_profile": True,
                              "offer_import_supported": True}, args.json)
            elif args.command == "history":
                print_result(store.history(config.profile_key, args.limit), args.json)
            elif args.command == "changes":
                print_result(store.notices(config.profile_key, args.all), args.json)
            elif args.command == "ack":
                print_result({"acknowledged": store.acknowledge(config.profile_key, args.ids)}, args.json)
            elif args.command == "watch":
                if args.watch_command == "add":
                    value = store.add_watch(args.url, args.title, args.note)
                elif args.watch_command == "update":
                    if args.status is None and args.note is None:
                        raise ValueError("Indique --status ou --note.")
                    value = store.update_watch(args.key, args.status, args.note)
                else:
                    value = store.watches(args.all)
                print_result(value, args.json)
            elif args.command == "export":
                print_result({"preferences": config.as_dict(), "status": store.status(config.profile_key),
                              "history": store.history(config.profile_key, None),
                              "notices": store.notices(config.profile_key, True),
                              "watchlist": store.watches(True),
                              "offers_by_audience": {audience: store.offers(config.profile_key, audience, include_inactive=True)
                                                     for audience in AUDIENCES},
                              "offer_events_by_audience": {audience: store.offer_events(config.profile_key, audience, limit=None)
                                                           for audience in AUDIENCES},
                              "limitation": PUBLIC_LIMIT}, True)
            elif args.command == "backup":
                destination = args.output or args.config.resolve().parent / "exports" / ("crous-personal-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
                if destination.suffix != ".sqlite3":
                    raise ValueError("Le fichier de sauvegarde doit porter l'extension .sqlite3.")
                print_result({"backup": store.backup(destination), "monitoring_started": False}, args.json)
            elif args.command == "import-offers":
                snapshot = read_offer_snapshot(args.file)
                print_result(store.import_offers(config, snapshot), args.json)
            elif args.command == "offers":
                print_result(store.offers(config.profile_key, args.audience, include_inactive=args.all), args.json)
            elif args.command == "offer-events":
                print_result(store.offer_events(config.profile_key, args.audience, limit=args.limit), args.json)
            elif args.command == "offer-history":
                print_result(store.offer_history(config.profile_key, args.audience, args.offer_key, limit=args.limit), args.json)
            elif args.command == "monitor":
                print("Surveillance locale au premier plan. Ctrl+C pour arrêter. Aucune notification externe.")
                print(PUBLIC_LIMIT)
                failures = 0
                while True:
                    value = perform_check(config, store)
                    if value.get("changed") or value.get("skipped"):
                        print_result(value, args.json)
                    failed = value.get("observation", {}).get("reason") in ("SOURCE_UNREACHABLE", "SOURCE_RATE_LIMITED")
                    failures = min(failures + 1, 5) if failed else 0
                    delay = max(config.interval_seconds, min(config.interval_seconds * (2 ** failures), 3600))
                    time.sleep(max(delay, value.get("retry_after_seconds", 0)))
        return 0
    except FileExistsError:
        print("Configuration déjà présente ; aucun fichier remplacé.", file=sys.stderr)
    except FileNotFoundError:
        print("Configuration absente. Lance d'abord : python -m crous_personal init", file=sys.stderr)
    except (ValueError, sqlite3.Error, OSError) as error:
        # Never print a request URL or credential-bearing exception here.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("Erreur locale : " + message, file=sys.stderr)
    except KeyboardInterrupt:
        print("\nSurveillance arrêtée.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
