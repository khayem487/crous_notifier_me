# Crous Notifier (Friend)

Petit projet Python pour surveiller une page Crous et envoyer une alerte dès qu'une disponibilité potentielle est détectée.

## Fonctionnalités

- Vérification périodique d'une page web Crous
- Analyse simple du texte de la page
- Détection d'état:
  - `AVAILABLE` (signaux de disponibilité)
  - `UNAVAILABLE` (aucune offre / complet)
  - `UNKNOWN` (ambigu)
- Notification Telegram optionnelle
- Mode `--once` pour un test unique
- Mémoire d'état locale (`state.json`) pour éviter le spam

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copie le fichier `.env.example` vers `.env` puis ajuste les variables:

```bash
cp .env.example .env
```

Variables principales:

- `CROUS_URL` : URL à surveiller
- `CHECK_INTERVAL_SEC` : fréquence de check (par défaut 300)
- `TELEGRAM_BOT_TOKEN` : token bot Telegram (optionnel)
- `TELEGRAM_CHAT_ID` : chat id Telegram (optionnel)

## Utilisation

### Test unique

```bash
python crous_notifier.py --once
```

### Mode continu

```bash
python crous_notifier.py
```

## Remarques

- Le parsing est volontairement simple et dépend du contenu texte rendu par la page.
- Si le site change fortement, adapter les listes de mots-clés dans `crous_notifier.py`.

---
Auteur: Khayem Ben Ghorbel
