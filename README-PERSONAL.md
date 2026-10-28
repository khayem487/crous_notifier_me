# Mon suivi CROUS — Cergy

Un outil **personnel et local** pour suivre la recherche Cergy, conserver les annonces et leurs changements, recevoir des alertes Windows et retrouver ses démarches. Les critères confirmés sont **Cergy, chambre, entrée dès disponibilité, sans plafond de loyer**. Aucun site public ni compte cloud n'est nécessaire.

## Mode principal : compagnon Edge

Le dossier `browser-extension/` contient le compagnon local. Il lit les résultats de la recherche officielle dans la session normale du navigateur, conserve son historique dans Edge et produit les notifications système. Aucun mot de passe ou cookie n'est extrait, copié ou envoyé au programme Python. Les identifiants MesServices restent gérés par le navigateur et le portail officiel.

Le compagnon et le suivi Python fonctionnent séparément : **aucun export/import manuel n'est nécessaire pour recevoir les alertes du compagnon**. L'import Python sert à archiver un relevé exporté, et la sauvegarde SQLite conserve le suivi avancé local.

Le contrôle est prévu toutes les cinq minutes, dans un onglet dédié. Le mode profil connecté est privilégié : si la session doit être rétablie, le suivi s'arrête et le signale, sans recharger la page pendant la connexion. Les résultats publics et ceux du compte restent séparés. Les dernières annonces ne sont pas effacées lorsque la page est indéterminée ou partielle.

Une annonce dont la ville, le type ou la date ne sont pas explicitement établis reste une **fiche à vérifier**. « Individuel » n'est pas transformé en « chambre », et une fourchette de prix ne devient pas un loyer exact. Le suivi ne dépose pas automatiquement de dossier et ne signe pas de réservation.

Pour l'installation et l'usage du compagnon, voir [son guide](browser-extension/README.md). Le navigateur doit rester ouvert et le PC éveillé pour ses contrôles ; après un redémarrage, le suivi reprend ses réglages enregistrés. L'installation seule ne lance pas la surveillance : il faut l'activer dans son interface.

L'installation dans Edge est manuelle, selon [le guide du compagnon](browser-extension/README.md). La recette du 27 octobre a confirmé l'installation sur ce PC, une notification Windows TEST reçue et un cycle automatique connecté. Après rétablissement de la session du portail et correction du lien de déconnexion contenant le nom du compte, le badge affichait **Relevé complet** et **Connecté confirmé** à 19 h 05, heure de Paris.

Au quotidien, laisse Edge et l'onglet suivi ouverts, avec le PC éveillé. Tu peux fermer ce chat. Le badge **Pause** arrête les contrôles sans effacer l'historique ; **Suivre cet onglet** reprend le suivi dans la recherche Cergy. Si une connexion est demandée, termine-la normalement avant de reprendre. La cadence peut être retardée par le navigateur ou la veille ; aucun fonctionnement PC éteint n'est promis.

Le zéro logement de la recette décrit uniquement le résultat affiché à cet instant. La détection d'une première vraie annonce positive reste à observer. Une pagination active conserve les fiches vues mais indique une couverture partielle ; les candidatures et réservations restent des démarches dans le site officiel.

## Suivi Python et sauvegarde locale

| Préférence initiale | Valeur |
| --- | --- |
| Zone | Cergy |
| Logement recherché | Chambre |
| Budget maximum | Aucun : `null` |
| Entrée | Dès disponibilité : `asap` |
| Source | Lien de recherche produit par le formulaire officiel Crous |
| Données | SQLite sur ce PC |
| Intervalle par défaut | 5 minutes, uniquement si `monitor` est lancé explicitement |

**La source configurée est publique, sans connexion.** Le portail indique que les offres accessibles varient selon le profil DSE. Un résultat public vide ne prouve donc pas l'absence de chambres accessibles après connexion. Aucune session, cookie ou donnée de profil DSE n'est lue ou stockée par cet outil.

Le type « chambre » et le budget sont des préférences conservées ; leur filtrage automatique n'est pas encore confirmé sur les annonces du portail. Le filtre « Individuel » n'est pas assimilé à une chambre. Aucun parseur d'annonces positives n'est annoncé comme validé sans annonce réellement observée.

## Ouvrir en un double-clic

Double-clique sur **`Crous-Cergy.cmd`** dans le dossier du projet. Il utilise l'environnement `.venv` existant et crée `cergy.json` uniquement si ce fichier manque. Le menu propose le statut, un contrôle ponctuel, l'historique et les alertes locales.

La surveillance ne commence que si tu choisis explicitement **5**. Elle reste dans la fenêtre ; Ctrl+C ou la fermeture de la fenêtre l'arrête. Aucun navigateur, service ou tâche automatique n'est ouvert. Les options **1**, **3** et **4** fonctionnent sans requête réseau. Le menu ne modifie pas tes préférences existantes.

Les commandes ci-dessous restent disponibles pour l'usage avancé. `Crous-Cergy.cmd status`, `changes` ou `history` permettent aussi d'appeler directement une consultation locale sans menu.

## Démarrer sous Windows

Depuis le dossier du projet, utilise l'environnement existant `.venv`. S'il n'existe pas encore :

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

La suite locale a été exécutée avec Python 3.14 ; le code utilise Python 3.12 ou plus récent. Il n'est pas nécessaire d'activer un script PowerShell ni de modifier la politique d'exécution.

Créer la configuration personnelle sans écraser un fichier existant :

```powershell
.\.venv\Scripts\python.exe -m crous_personal init
```

Cela crée `cergy.json`, avec les préférences ci-dessus et le lien Cergy confirmé depuis le formulaire officiel. Ce fichier personnel est ignoré par Git. `cergy.example.json` est le modèle de référence ; aucun budget n'est supposé.

Effectuer **un seul contrôle**, puis consulter ce qui a été conservé :

```powershell
.\.venv\Scripts\python.exe -m crous_personal check
.\.venv\Scripts\python.exe -m crous_personal status
.\.venv\Scripts\python.exe -m crous_personal history
.\.venv\Scripts\python.exe -m crous_personal changes
```

`status`, `history` et `changes` ne contactent pas le Crous. L'historique et les notifications locales survivent aux redémarrages. Même avec deux processus, la même base impose **300 secondes entre deux contrôles réseau** ; un contrôle trop proche restitue le statut mémorisé avec `LOCAL_COOLDOWN` au lieu de refaire une requête.

## Archiver les annonces du compagnon

Le bouton « Exporter le dernier relevé » du compagnon produit un fichier **snapshot v1** sans identifiants. Ce fichier peut être archivé dans SQLite, sans accès réseau :

```powershell
.\.venv\Scripts\python.exe -m crous_personal import-offers "CHEMIN_DU_RELEVE.json"
.\.venv\Scripts\python.exe -m crous_personal offers
.\.venv\Scripts\python.exe -m crous_personal offer-events
```

Ces consultations concernent par défaut le profil `AUTHENTICATED`. Ajouter `--audience PUBLIC_ONLY` pour consulter les données publiques. `offers --all` inclut les fiches qui ne sont plus listées. Un relevé partiel ou une page inconnue ne fait jamais disparaître une annonce ; seule une collecte complète et cohérente du même périmètre peut le faire. Un fichier identique n'est pas appliqué deux fois ; une ancienne collecte ne remplace pas une plus récente.

La migration SQLite vers la version 2 ajoute les tables des annonces sans remettre à zéro l'historique, les notes ni les suivis existants. Une sauvegarde cohérente du fichier local a été créée avant son premier passage en version 2.

## Lire le résultat

| État | Signification |
| --- | --- |
| `NO_PUBLIC_RESULTS` | La recherche officielle contient explicitement « Aucun logement trouvé pour Cergy ». C'est le résultat public de ce contrôle, pas une conclusion sur les offres DSE. |
| `UNKNOWN` + `RESULTS_REQUIRE_MANUAL_REVIEW` | Le contenu n'est plus le zéro public connu ou son format n'est pas reconnu. Une notification locale `REVIEW_REQUIRED` invite à ouvrir le lien ; aucune disponibilité ni chambre n'est inventée. |
| `UNKNOWN` + `SOURCE_UNREACHABLE` / `SOURCE_RATE_LIMITED` | Le contrôle a échoué. Le dernier résultat fiable reste conservé avec son ancien horodatage. |

La transition **zéro public → résultat différent** n'est pas ignorée. Elle produit une notification locale à vérifier, dédupliquée si la même page revient après un redémarrage. Elle n'est pas présentée comme une chambre disponible. Les erreurs de source ont également un historique distinct.

Marquer une notification locale comme lue, avec son identifiant affiché :

```powershell
.\.venv\Scripts\python.exe -m crous_personal ack 1
```

## Suivre ses liens et démarches

Le suivi est manuel : ajouter un lien officiel observé ne vérifie pas sa disponibilité et ne dépose aucune candidature.

```powershell
.\.venv\Scripts\python.exe -m crous_personal watch add "LIEN_OFFICIEL_PUBLIC" --title "Titre de mon suivi" --note "Mes remarques"
.\.venv\Scripts\python.exe -m crous_personal watch list
.\.venv\Scripts\python.exe -m crous_personal watch update PREFIXE_ID --status interested --note "À examiner"
```

Remplace les valeurs en majuscules par ton lien ou l'identifiant affiché. Les statuts sont `watching`, `interested`, `applied` et `dismissed`. `applied` est uniquement une note personnelle sur une démarche faite ailleurs. Ajouter deux fois le même lien ne détruit ni la note ni le statut existants. Les liens contenant des tokens ou paramètres de session sont refusés.

## Exporter et sauvegarder

```powershell
.\.venv\Scripts\python.exe -m crous_personal export > data\export.json
.\.venv\Scripts\python.exe -m crous_personal backup
```

L'export contient tout l'historique de la recherche active, les notifications, les suivis, les annonces et leurs événements dans les deux périmètres d'audience. La sauvegarde SQLite cohérente est créée dans `exports/` ; elle inclut les données validées encore présentes dans le journal WAL. Un fichier de sauvegarde existant n'est pas remplacé.

`data/`, `exports/`, `cergy.json`, `*.personal.json`, les fichiers SQLite et les exports personnels prévus sont ignorés par Git. Ne mets pas les notes ou exports dans un autre fichier destiné à être publié. Aucun export de cookies ou de profil navigateur n'est nécessaire.

## Surveillance locale facultative

Rien ne tourne automatiquement après `init`, `check` ou l'installation. Pour surveiller au premier plan, uniquement quand tu le décides :

```powershell
.\.venv\Scripts\python.exe -m crous_personal monitor
```

La boucle reste attachée au terminal et s'arrête avec **Ctrl+C**. Le PC doit rester éveillé. Les répétitions identiques ne créent pas de nouvelles notifications locales ; après une erreur réseau, le délai augmente progressivement. Aucun service Windows, tâche planifiée, cron, email ou Telegram n'est créé. Cette boucle n'a pas été lancée pendant la préparation du projet.

## Vérifier le projet

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Les tests couvrent le résultat public réellement observé, le passage à un résultat à vérifier, l'absence de faux positifs de chambres, la persistance, le dédoublonnage après redémarrage, le délai entre processus, le rollback SQLite, les sauvegardes et les liens avec tokens refusés. Les tests ne réalisent aucun appel Crous ni aucun envoi externe.

La suite Python compte 71 tests réussis au 27 octobre 2026. Le compagnon possède sa suite Node séparée de 75 tests ; aucune détection positive réelle n'est déduite de ces essais hors navigateur.

L'ancienne interface Flask locale reste disponible et documentée dans [README-WEB.md](README-WEB.md), mais elle ne constitue pas une démo à publier. Le script historique `crous_notifier.py` conserve son mode Telegram séparé ; le nouveau module personnel ne le configure ni ne le lance.
