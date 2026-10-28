# Crous Notifier

**Usage actuel : suivi personnel local pour Cergy, avec préférence chambre, entrée dès disponibilité et aucun budget imposé.** Le [compagnon Edge](browser-extension/README.md) surveille la recherche dans la session normale du navigateur et envoie les notifications Windows. Le [suivi SQLite](README-PERSONAL.md) permet aussi d'archiver les relevés et les démarches.

| Au quotidien | Fonctionnement |
| --- | --- |
| Démarrer | Ouvrir la recherche Cergy connectée, puis cliquer **Suivre cet onglet** dans le badge local. |
| Vérifications | Environ toutes les cinq minutes, Edge ouvert et PC éveillé. Ce chat n'a pas besoin de rester ouvert. |
| Changements | Notifications locales dédupliquées pour les fiches nouvelles ou modifiées ; historique conservé dans Edge. |
| Session expirée | Surveillance suspendue : se reconnecter dans le portail, puis reprendre depuis le badge. |
| Plusieurs pages | Relevé partiel : seule la page courante est suivie. Les informations absentes restent à vérifier. |
| Candidature | Ouvrir la fiche officielle pour vérifier la disponibilité et effectuer la démarche. |

La recette du 27 octobre a confirmé une notification Windows reçue et un cycle automatique avec **Connecté confirmé / Relevé complet**. La recherche affichait zéro logement à cet instant ; ce résultat n'est pas une mesure de disponibilité actuelle.

Le module `python -m crous_personal` n'envoie aucun message externe. La source publique et les offres accessibles après connexion DSE restent distinguées. Aucun site de démonstration ni déploiement cloud n'est nécessaire.

## Script historique

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

Une page inaccessible ou un résultat `UNKNOWN` conserve le dernier état fiable et n'envoie aucune alerte. Si Telegram est configuré mais que l'envoi échoue, la transition n'est pas acquittée : le contrôle fiable suivant peut réessayer. Les journaux d'erreur ne recopient pas les URL pouvant contenir le token du bot.

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

- Le parsing dépend du texte réellement reçu. Les mots de navigation seuls ne prouvent pas une disponibilité ; un nombre explicite est nécessaire.
- Une page d'accueil, une application JavaScript ou une page inaccessible peut donc rester `UNKNOWN`. Le script ne prétend pas avoir lancé une recherche par ville.
- L'interface web préparée permet de renseigner un lien de recherche officiel, sa zone et ses critères : voir [README-WEB.md](README-WEB.md). Elle ne démarre ni surveillance continue ni notification et n'est pas publiée à ce stade.

---
Auteur: Khayem Ben Ghorbel
