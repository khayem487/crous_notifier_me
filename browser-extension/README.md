# Mon suivi CROUS · Cergy — extension Edge locale

Une extension personnelle Manifest V3 pour suivre les résultats du portail dans votre session habituelle. Ce dossier est autonome : historique, déduplication et notifications restent dans le navigateur. L'import Python est une sauvegarde facultative, pas une étape nécessaire au fonctionnement.

## État et limites de la livraison

Le code, les tests hors navigateur et l'aperçu visuel peuvent être vérifiés sans installation. Cela ne prouve pas une détection positive réelle ni une session DSE fonctionnelle. Les sélecteurs de cartes sont issus des assets officiels observés ; aucune carte positive réelle n'a encore servi de preuve. Les fixtures positives et l'aperçu portent explicitement la mention TEST et des logements fictifs.

Critères initiaux : Cergy, préférence chambre, entrée dès que possible, aucun budget maximum, 300 secondes entre deux vérifications. La date d'entrée ASAP est une préférence ; une date de disponibilité n'est jamais inventée depuis une carte.

Par défaut, le suivi exige un périmètre connecté explicitement confirmé. Une page seulement publique ou non confirmée déclenche une alerte unique et suspend la surveillance. Terminez la connexion manuellement dans le portail, puis utilisez Reprendre. Le mode Recherche publique uniquement doit être choisi explicitement dans Réglages ; un zéro public ne signifie pas zéro offre accessible au dossier DSE.

## Installation manuelle dans Edge

1. Vérifier les tests, le manifeste et les permissions ci-dessous.
2. Dans Edge, ouvrir edge://extensions, activer Mode développeur, choisir Charger l'extension non empaquetée et sélectionner ce dossier contenant manifest.json. L'archive dans dist doit être décompressée avant cette sélection. Ces manipulations sont à faire par le propriétaire du navigateur : l'outil de contrôle refuse les pages internes edge://, et aucune méthode de contournement n'est utilisée.
3. Ouvrir la fenêtre de l'extension. Vérifier les critères, puis Démarrer. Un onglet dédié est créé sans modifier les autres onglets.
4. Si une connexion ou une vérification humaine est nécessaire, la surveillance s'arrête. Aucun mot de passe, code, cookie ou CAPTCHA n'est saisi par l'extension. Après avoir retrouvé la recherche Cergy dans la session normale, cliquer Reprendre dans la popup ou Suivre cet onglet dans le badge local.
5. Vérifier une vraie notification Windows, la source/périmètre affiché et un cycle de 300 secondes. Cette recette réelle reste nécessaire avant d'affirmer que l'extension est opérationnelle sur le poste.

Procédure Microsoft : https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading

Après installation manuelle, un petit badge est présent uniquement sur l'URL Cergy exacte. Il donne l'état, le périmètre et la date du contrôle, avec Suivre cet onglet, Pause, Tester l'alerte et Actualiser l'état. Un clic Suivre cet onglet adopte explicitement cet onglet de recherche ; il ne recharge pas une autre page ou un formulaire de connexion. Le badge est repliable, utilise un shadow DOM fermé et n'expose ni fiches ni configuration au contexte global du site. Les actions actives exigent un vrai événement utilisateur ; aucun window.message, transport réseau ou commande d'import n'est exposé par ce badge. Il ne gère aucune installation, extension ou préférence du navigateur.

## Ce que la lecture permet d'affirmer

Seules les cartes .fr-card dans le contenu principal sont analysées : lien .fr-card__title a vers /tools/{outil}/accommodations/{id}, titre de résidence, adresse p.fr-card__desc, prix affiché p.fr-badge. Le prix de la carte est agrégé ; une fourchette reste une fourchette et n'est pas présentée comme le loyer exact d'une chambre.

Un mode Individuel, une résidence, un prix ou une surface ne prouvent pas un type chambre. Le type reste inconnu sauf libellé explicite Type de logement : Chambre, Studio ou Appartement. Cette reconnaissance future de libellé est testée par fixture fictive, sans prétendre qu'un tel libellé positif a déjà été observé dans le portail. La ville reste inconnue sans adresse explicite à Cergy. Les surfaces en fourchette et les dates absentes ne sont pas transformées en valeur exacte.

Un relevé COMPLETE exige un périmètre confirmé, un compteur visible unique cohérent avec les fiches dédupliquées et aucune page suivante active. Un relevé PARTIAL conserve les fiches vues mais ne permet jamais de déduire une disparition. Une erreur, un format inconnu ou une connexion manquante conserve les dernières données fiables. Public et connecté ont des historiques et comparaisons séparés. Les alertes de nouvelles fiches, prix et informations modifiés sont dédupliquées ; le passage à un type chambre et une ville Cergy explicitement indiqués est signalé séparément. Une fiche de type ou ville inconnu est toujours annoncée à vérifier.

Le périmètre connecté exige un lien de déconnexion dans le vrai header, même si le menu est replié, sans libellé Identification/Connexion contradictoire. Le lien officiel `/mse/oidc/local-logout`, observé le 27 octobre dans une vraie session, est reconnu même si le nom du compte précède « Déconnexion ». Ce nom n'est pas exporté. Un lien étranger ou un faux header dans une carte ne peut pas établir ce périmètre. La connexion à MesServices seule n'est pas une preuve de connexion au portail logement.

Cette version ne parcourt pas automatiquement les autres pages. Une pagination active ou un compteur incohérent donne PARTIAL et la fenêtre indique que seule la page courante est couverte. Aucune détection exhaustive de toutes les pages n'est revendiquée.

Les onglets de recherche en cours de connexion ne sont pas rechargés ou renavigués automatiquement. Seul l'identifiant de l'onglet créé par l'extension est manipulé, avec validation de son URL avant toute lecture ou recharge. Les fiches s'ouvrent sur clic dans le site officiel ; le code ne remplit aucun formulaire, ne candidate et ne réserve pas.

Une erreur serveur explicite, dont la page nue « 500 » observée sur le portail, donne SOURCE_UNAVAILABLE. Elle ne prouve ni une expiration de session ni une recherche vide. Les fiches fiables restent conservées et le suivi réessaie à sa cadence normale, sans boucle de rechargement. Une page de connexion ou un contrôle humain explicitement identifié suspend toujours le suivi.

## Données et permissions

- storage : critères, fiches, relevés et événements locaux, pas de synchronisation cloud.
- alarms : contrôle toutes les cinq minutes ou davantage, avec délai et verrou persistés entre réveils du worker.
- notifications : alertes Windows dédupliquées, soumises aux réglages de notification du système.
- Seul hôte autorisé : https://trouverunlogement.lescrous.fr/* ; aucun accès MesServices, email ou autre site. Le content script ignore immédiatement toute route autre que la recherche Cergy exacte.
- Aucun accès cookies, mots de passe, historique de navigation, debugger ou candidature. Les seules ressources exposées au site sont les modules de lecture et de contrôle statiques, sans état personnel.

Les alarmes peuvent être retardées par Edge ou le sommeil du PC et ne réveillent pas la machine. Elles sont rétablies au démarrage du worker si nécessaire ; il n'y a pas de promesse de surveillance navigateur fermé. Les échecs de notification ne font pas disparaître l'historique local.

Les notifications utilisent une file locale persistée avant l'appel système. L'alerte n'est acquittée et retirée de cette file qu'après succès de création ; en cas de refus ou d'erreur, Alertes bloquées et le nombre en attente restent visibles. Un démarrage du worker, un prochain contrôle ou Réessayer effectue au plus trois tentatives et s'arrête au premier échec, sans boucle. L'identifiant natif reste stable lors d'un retry. Une notification déjà acquittée n'est pas renvoyée après redémarrage. Une interruption exactement entre création système et écriture de l'accusé peut réutiliser ce même identifiant : aucune promesse de transaction atomique avec Windows n'est faite. Tester l'alerte locale n'ouvre aucun onglet CROUS et indique clairement qu'aucun logement n'a été détecté.

Les données sont bornées : 300 lignes de contrôles, 500 événements, jusqu'à 1000 fiches inactives archivées en plus des fiches connues. Aucune donnée n'est effacée parce qu'un résultat est UNKNOWN. Une désinstallation peut supprimer le stockage local : sauvegarder avant de la faire.

## Export et sauvegarde

Exporter le dernier relevé produit crous-cergy-snapshot-v1-DATE.json, au contrat strict v1 partagé avec la commande Python import-offers. Il contient uniquement les champs source, zone, périmètre, horodatage, complétude et offres normalisées ; aucun HTML, profil, cookie ou jeton. Un import de relevé est signalé comme import local et ne déclenche ni réseau ni notification. Un fichier plus ancien ne remplace pas les résultats plus récents.

Sauvegarder l'historique du navigateur produit crous-cergy-backup-DATE.json, format de sauvegarde distinct incluant critères, fiches, contrôles et événements. Ce fichier n'est pas un snapshot importable dans Python. Cette version ne fournit pas de restauration automatique d'une sauvegarde complète ; les fichiers restent des archives lisibles et les snapshots v1 sont importables.

## Développer et préparer

Node 22 ou plus récent : npm ci --ignore-scripts, puis npm test et npm run validate. Les tests utilisent des documents DOM fictifs et une API Chrome simulée, sans navigateur ni requête CROUS. L'icône originale est reproductible avec node scripts/icons.js. PowerShell : ./scripts/package.ps1 crée l'archive source installable dans dist, sans node_modules ni tests.

Au 27 octobre 2026, les 75 tests Node passent sans test ignoré. Cela couvre les commandes du badge, l'isolation des onglets/documents, les alertes en attente, la persistance, la lecture, les erreurs de source et le lien de compte réellement observé. Ces tests ne remplacent pas l'installation et la notification réelles sur le poste.

Pour revoir visuellement la popup sans installation, servir ce dossier par un serveur statique local puis ouvrir popup/index.html dans Edge via Cua. Le bandeau TEST reste visible, les logements sont fictifs et les actions sur le portail sont désactivées. Ce mode n'installe rien et ne prouve pas les API d'extension.

Références : https://developer.chrome.com/docs/extensions/reference/api/tabs — https://developer.chrome.com/docs/extensions/reference/api/alarms — https://developer.chrome.com/docs/extensions/how-to/test/unit-testing
