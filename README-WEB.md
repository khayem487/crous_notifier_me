# Crous Notifier — recherche publique configurable

> Interface locale préparée pendant une étape précédente, sans publication. L'objectif actuel est l'outil personnel Cergy décrit dans [README-PERSONAL.md](README-PERSONAL.md) ; cette interface n'est pas une démonstration destinée au portfolio et ne consulte pas le profil DSE.

Cette version réutilise le parseur Python du projet derrière une interface Flask. Le formulaire reçoit le lien d'une recherche publique copiée depuis le portail Crous, sa zone et un texte de critères facultatif. Aucun état de disponibilité n'est prérempli et aucun format d'API interne n'est supposé.

## Périmètre précis

- Un seul domaine exact est autorisé : `trouverunlogement.lescrous.fr`, en HTTPS. Le chemin et la query du lien officiel sont conservés. Les autres domaines, userinfo, ports explicites, fragments, caractères de contrôle et clés de query courantes contenant des identifiants de session/tokens sont refusés. Les redirections ne sont pas suivies et la réponse est limitée à 1 Mo.
- Le lien détermine la recherche effectuée par le portail. La zone et les critères sont des descriptions à confirmer dans le texte reçu ; ils ne sont pas convertis en paramètres inconnus ni appliqués artificiellement aux résultats. Un signal chiffré reste `UNKNOWN` si ces descriptions ne sont pas retrouvées. Ce garde-fou textuel ne prouve pas à lui seul le comportement des filtres d'une application JavaScript.
- Les mots « offres », « disponible » ou « disponibilité » ne sont plus des preuves. Le parseur exige un nombre explicite d'offres/logements disponibles, reconnaît l'absence explicite de résultats et retourne `UNKNOWN` en cas de contradiction ou d'information insuffisante.
- Le texte des scripts et de la navigation est retiré. Sans configuration, seule la page d'accueil est consultée, sans zone implicite. Le dépôt d'origine ne précisait aucune ville ; les noms de ville présents dans les tests sont des fixtures, sans observation de disponibilité.
- Le chargement du site ne lance aucun appel Crous. Le texte reçu est réutilisé par URL pendant deux minutes, puis analysé selon les descriptions de chaque requête. Un délai de deux minutes entre deux URL non mises en cache est partagé par toutes les requêtes **sur la même instance** ; un changement de query ne contourne donc pas ce délai. Ce cache mémoire et cette limite ne sont pas partagés entre plusieurs instances cloud. Aucun cron n'est démarré.
- Les réponses de recherche sont `Cache-Control: no-store` : une zone ou des critères envoyés dans le formulaire ne sont pas placés dans un cache CDN partagé. Le formulaire n'est pas conservé en base et aucun historique persistant n'est créé.
- La version web ne charge pas `.env`, ne lit pas les identifiants Telegram, n'appelle pas `send_telegram`, ne crée pas `state.json` et n'envoie aucun email ou message.
- Le notifier CLI historique reste dans le dépôt. Son mode continu et ses éventuelles notifications nécessitent une configuration séparée ; ils ne sont jamais démarrés par le site web.
- Compatibilité CLI : `fetch_page_text()` conserve l'URL `CROUS_URL` de confiance configurée localement, y compris chemin et paramètres. Cette fonction n'est pas exposée par le web. Le transport web borné est commun au contrôle d'accueil et aux recherches validées.

## Modèle et API

`CrousSearch` contient `source_url`, `zone` (100 caractères maximum) et `criteria` (300 maximum, facultatif). Une recherche autre que l'accueil exige une zone. Une zone seule avec le lien d'accueil est refusée : elle n'aurait pas été appliquée par la source.

Le formulaire appelle `POST /api/check` avec ces trois champs JSON. Le corps est limité à 8192 octets, l'URL à 2048 caractères. Les critères inconnus ou les champs supplémentaires sont refusés. La réponse inclut le modèle choisi, l'horodatage de la source et les indicateurs de correspondance textuelle. Une nouvelle URL pendant le délai reçoit HTTP 429 avec `Retry-After`, sans nouvelle requête Crous.

`GET /api/check?source=official` reste compatible avec le contrôle ponctuel de l'accueil ; il n'accepte pas une URL arbitraire dans la query. `/health` ne contacte pas le portail et confirme que notifications et surveillance continue sont désactivées.

## Exécuter et vérifier

Installer `requirements.txt`, puis `python -m flask --app app run --host 127.0.0.1 --port 8094`. Tester avec `python -m unittest discover -s tests -v`.

Les tests couvrent faux positifs et contradictions, URL/domaine, redirections, compatibilité CLI, critères manquants, API et taille des requêtes, cache par source et délai entre recherches, ainsi que l'absence d'appels Telegram/configuration/état. Ils utilisent des réponses factices explicitement isolées ; aucun chemin figurant dans une fixture ne prétend être un endpoint officiel et aucune requête Crous n'est envoyée par ces tests.

`app.py` et `vercel.json` restent des fichiers de préparation antérieurs : **aucune démo ni publication n'est demandée**. Le véritable lien Cergy et son zéro public ont depuis été validés dans le navigateur et par HTTP ; cela ne valide ni les offres après connexion DSE ni un parseur d'annonces positives. L'outil personnel actuel est documenté dans [README-PERSONAL.md](README-PERSONAL.md).
