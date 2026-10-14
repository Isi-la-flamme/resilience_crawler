# Yennenga Crawl — prototype Open Data

Yennenga Crawl est un prototype local qui collecte des pages et des documents
depuis les sites configurés, prépare leur contenu pour la recherche, puis
propose une API en lecture seule et une interface web de consultation.

Ce projet est un outil de collecte et d’exploration. **Ce n’est pas un catalogue
Open Data officiel et il ne détermine pas si les contenus peuvent être
réutilisés.** Le statut des droits est actuellement `not_assessed` (« non
évalué »).

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Chaîne de traitement](#chaîne-de-traitement)
- [Prérequis et installation](#prérequis-et-installation)
- [Démarrage rapide](#démarrage-rapide)
- [Configuration du crawler](#configuration-du-crawler)
- [Commandes](#commandes)
- [API et tableau de bord](#api-et-tableau-de-bord)
- [Fichiers produits](#fichiers-produits)
- [Tests](#tests)
- [Droits, provenance et précautions](#droits-provenance-et-précautions)
- [Structure du projet](#structure-du-projet)
- [Dépannage](#dépannage)

## Fonctionnalités

- Crawl multi-sites à partir d’URL de départ définies dans `config.yaml`.
- File d’URL persistante dans SQLite, collecte reprenable après interruption.
- Réglage des pages maximales, workers et retries par configuration ou par
  lancement.
- Actualisation des pages déjà collectées avec `--refresh` et validateurs HTTP
  conditionnels lorsque le serveur les fournit.
- Exclusion des URL dont le dernier segment est `pricing` ou `token`, ou dont
  le chemin répète l’un des segments `pricing`, `contact` ou `token`.
- Enregistrement des pages HTML, images et documents désactivable
  indépendamment avec `--no-html`, `--no-images` et `--no-documents`.
- Export Excel des pages, images et documents.
- Nettoyage du HTML en texte brut, avec exclusion des éléments non éditoriaux
  et détection de blocs répétitifs par site.
- Extraction et recherche plein texte des PDF, avec SQLite FTS5.
- API FastAPI en lecture seule et tableau de bord web local.

## Chaîne de traitement

Le flux habituel est le suivant :

```text
sites institutionnels
        │
        ▼
   cli.py / crawler
        ├── crawl.db       pages, file d’URL, images et documents
        ├── img/           images téléchargées
        └── documents/     documents téléchargés
              │
              ├── cleaner.py ──────► clean_crawl.db
              ├── pdf_index.py ────► pdf-index.db
              └── cli.py --export-excel ─► crawl-export.xlsx
                                         
clean_crawl.db + pdf-index.db
              │
              ▼
        api.py ──► tableau de bord et API locale
```

Le cleaner et l’indexeur lisent la base de collecte sans la modifier et créent
des bases de sortie distinctes. L’API consulte les bases préparées ; elle ne
sert ni les bases SQLite elles-mêmes ni les fichiers locaux des documents.

## Prérequis et installation

- Windows avec PowerShell (les commandes ci-dessous utilisent les chemins
  habituels du dépôt).
- Python **3.10 ou plus récent**.
- Accès réseau aux sites configurés pour effectuer un crawl.

Depuis la racine du dépôt, créer un environnement virtuel et installer les
dépendances :

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Pour exécuter les tests, installer également les dépendances de développement :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Les dépendances principales sont `openpyxl` (export Excel), `pypdf`
(traitement des PDF), `PyYAML` (configuration YAML), `fastapi` et `uvicorn`
(API et serveur local).

## Démarrage rapide

Les commandes suivantes sont à exécuter depuis la racine du projet.

1. **Lancer ou reprendre la collecte** :

   ```powershell
   .\.venv\Scripts\python.exe cli.py --config config.yaml
   ```

   Le chemin est facultatif : `python cli.py --config` charge également
   `config.yaml` par défaut. Pour utiliser un autre fichier, indiquez son chemin
   après `--config`.

   Pour conserver les URL d'images découvertes sans télécharger leurs fichiers :

   ```powershell
   .\.venv\Scripts\python.exe cli.py --config --no-images
   ```

   Le crawler crée ou réutilise `crawl.db`, `img/`, `documents/` et
   `crawler.log.jsonl`. Sans `--config`, la commande ne démarre pas de nouveaux
   sites : elle ne reprend que les sites ayant une file en attente dans SQLite.

2. **Préparer le texte des pages** :

   ```powershell
   .\.venv\Scripts\python.exe cleaner.py
   ```

   Par défaut, la commande lit `crawl.db` et crée ou complète `clean_crawl.db`.

3. **Créer l’index plein texte des PDF** :

   ```powershell
   .\.venv\Scripts\python.exe pdf_index.py index
   ```

   Par défaut, la commande lit les documents référencés dans `crawl.db`, extrait
   le texte des PDF téléchargés et crée `pdf-index.db`.

4. **Démarrer le portail local** :

   ```powershell
   .\.venv\Scripts\python.exe api.py
   ```

   Ouvrir <http://127.0.0.1:8000/> pour le tableau de bord ou
   <http://127.0.0.1:8000/docs> pour la documentation interactive de l’API.

Les étapes de nettoyage et d’indexation sont indépendantes l’une de l’autre.
Pour que le tableau de bord soit pleinement alimenté, exécuter chacune d’elles
après la collecte.

## Configuration du crawler

`config.yaml` définit les URL initiales, les limites et les chemins de sortie.
Les fichiers `.json` restent lisibles si un chemin JSON est explicitement
fourni à `--config` ; le fichier par défaut est désormais YAML.
Les chemins relatifs sont résolus par rapport au dossier contenant le fichier
de configuration.

| Clé | Valeur actuelle | Description |
| --- | --- | --- |
| `start_urls` | Sites institutionnels dans `config.yaml` | URL de départ de chaque crawl. |
| `max_pages` | `1000` | Nombre maximal de pages traitées par site. |
| `workers` | `2` | Nombre de workers de collecte. |
| `delay` | `0.5` | Délai configuré par le crawler entre les requêtes. |
| `image_batch_size` | `1` | Taille des lots de téléchargement des images. |
| `timeout` | `30` | Délai d’attente réseau, en secondes. |
| `retries` | `2` | Nombre de reprises après la tentative initiale. |
| `max_response_bytes` | `52428800` | Taille maximale d’une réponse, en octets (50 Mio). |
| `database_file` | `crawl.db` | Base SQLite de collecte. |
| `database_flush_interval` | `30` | Intervalle de sauvegarde de la base, en secondes. |
| `images_dir` | `img` | Dossier des images téléchargées. |
| `documents_dir` | `documents` | Dossier des documents téléchargés. |
| `log_file` | `crawler.log.jsonl` | Fichier de journalisation. |
| `crawler_name` | `Yennenga Crawl` | Nom attribué au crawler. |
| `log_level` | `INFO` | Niveau de journalisation. |

Les documents (PDF et autres formats reconnus) sont plafonnés à **20 Mio**
(20 × 1024 × 1024 octets), sans dépasser `max_response_bytes`. Si la taille
annoncée ou reçue dépasse cette limite, le document est ignoré sans retry et
son URL passe au statut `skipped` dans la file SQLite ; il ne sera donc pas
repris automatiquement au prochain crawl. Cette règle ne change pas la limite
appliquée aux pages HTML et aux autres réponses.

Les valeurs CLI `--max-pages`, `--workers` et `--retries` remplacent la
configuration uniquement pour le lancement courant. `--retries 0` désactive
les reprises après la tentative initiale. Vérifier les règles d’accès des sites
et choisir une cadence raisonnable avant toute collecte.
L’option `--no-images` désactive les téléchargements d’images pour ce lancement ;
les URL des images restent extraites et enregistrées avec les pages.
L’option `--no-html` évite l’enregistrement des pages HTML. Le crawler les
analyse tout de même afin de découvrir les liens, images et documents à traiter.
L’option `--no-documents` évite le téléchargement et le stockage des documents
reconnus pour ce lancement. Les URL documentaires identifiées par leur extension
sont ignorées avant la requête ; si le document n’est reconnaissable qu’au type
MIME, le crawler s’arrête après les en-têtes HTTP, sans lire le corps. Ces URL
passent à l’état `skipped` et ne seront pas reprises automatiquement.

Les URL contenant au moins deux segments de chemin identiques parmi `pricing`,
`contact` et `token` sont ignorées avant toute requête et passent à l’état
`skipped`. Les routes qui se terminent par `pricing` ou `token` restent
également exclues, conformément à la règle existante. Une seule occurrence de
`contact` n’est pas exclue.
Au moins un type de contenu doit rester activé ; désactiver simultanément HTML,
images et documents affiche une erreur claire avant le démarrage de la collecte.

Une URL qui échoue après les retries configurés garde le statut `failed` et
n’est pas remise en attente automatiquement au prochain lancement. Pour
retenter explicitement toutes les URL en échec :

```powershell
.\.venv\Scripts\python.exe cli.py --retry-failed
```

Les URL interrompues alors qu’elles étaient en cours de téléchargement sont
remises en file automatiquement au lancement suivant.

## Commandes

### Collecter les sites configurés

```powershell
.\.venv\Scripts\python.exe cli.py --config config.yaml
```

Sans option `--config`, `cli.py` ne démarre pas un nouveau crawl : il reprend
uniquement les sites qui ont des URL en attente dans la base SQLite. Si aucun
site n’est en attente, la commande affiche le rappel ci-dessus.

Avec `--config`, le crawler ne sélectionne que les sites déclarés dans le YAML.
Il reprend leurs URL en attente dans SQLite. Pour chaque URL de départ sans
travail en attente, il initialise la file depuis les liens déjà enregistrés ;
si aucune page du site n’existe encore, il démarre depuis le `start_url`. Les
files en attente de sites absents du YAML ne sont pas traitées par ce lancement.
Elles restent en état `queued` dans SQLite pour une reprise ultérieure sans
configuration ou après ajout du site au YAML. Les URL déjà terminées ou en échec
ne sont pas réactivées.
Chaque `start_url` définit sa portée : son domaine et ses sous-domaines sont
crawlés ; les autres domaines doivent être explicitement ajoutés au YAML. Les
liens et images externes, y compris ceux déjà présents dans la file SQLite,
sont ignorés. Les redirections HTTP hors de cette portée sont refusées.
Les liens écrits comme un nom d’hôte nu (par exemple `www.exemple.bf` sans
schéma) sont résolus vers la racine du site, et les anciens chemins mal formés
qui se terminent par ce nom d’hôte sont ignorés.

Si la file d’un site a été supprimée mais que ses pages restent en base,
l’initialisation reconstruit la file depuis les liens sauvegardés de ces pages.
Les pages déjà enregistrées restent exclues du téléchargement ; le crawler
continue avec leurs liens encore inconnus.

Fournir une ou plusieurs URL de départ pour ce lancement seulement :

```powershell
.\.venv\Scripts\python.exe cli.py https://primature.gov.bf/
.\.venv\Scripts\python.exe cli.py https://primature.gov.bf/ https://gouvernement.gov.bf/
```

Les URL fournies remplacent les `start_urls` de la configuration pour cette
exécution. Le crawler reprend les files existantes pour ces sites ; si aucune
URL n’y est en attente, il ajoute les URL données comme points de départ.

Utiliser une autre configuration ou modifier les paramètres pour ce lancement
seulement :

```powershell
.\.venv\Scripts\python.exe cli.py --config .\config.yaml --max-pages 200 --workers 4 --retries 1
```

Actualiser les pages déjà enregistrées lorsque le serveur fournit des
validateurs HTTP :

```powershell
.\.venv\Scripts\python.exe cli.py --refresh
```

La base conserve la file d’URL. En cas d’interruption avec `Ctrl+C`, le crawler
arrête la planification, sauvegarde les données terminées et laisse les URL
restantes disponibles pour une reprise ultérieure. Les URL déjà enregistrées
comme pages ou documents ne sont pas réclamées une seconde fois. Avec
`--refresh`, les sites sont ceux fournis en argument ou, à défaut, ceux de la
configuration.

### Exporter la collecte vers Excel

```powershell
.\.venv\Scripts\python.exe cli.py --export-excel crawl-export.xlsx
```

Cette option exporte la base configurée sans lancer de crawl. Un chemin de
sortie relatif est résolu à partir du dossier de la base SQLite. Le classeur
contient trois feuilles : `Pages`, `Images` et `Documents`. Le texte dépassant
la limite d’une cellule Excel est tronqué et signalé dans la colonne
`Contenu tronqué`.

### Nettoyer les pages

```powershell
.\.venv\Scripts\python.exe cleaner.py
```

Choisir explicitement les bases source et sortie :

```powershell
.\.venv\Scripts\python.exe cleaner.py --source-db .\crawl.db --output-db .\clean_crawl.db
```

Le nettoyage retire notamment les éléments `head`, scripts, styles, navigation,
en-têtes, pieds de page, formulaires et autres éléments non éditoriaux. Il
cherche aussi des blocs répétés sur au moins 80 % des pages d’un site (avec un
minimum de trois pages). Après nettoyage, les pages dont le texte exploitable
fait **moins de 500 caractères** sont exclues de la base nettoyée ; une page de
500 caractères exactement est conservée. La base source de collecte n’est pas
modifiée. Le texte destiné à la recherche est disponible dans la colonne
`cleaned_text` de la base créée. Les blocs détectés et les statistiques par
site sont conservés dans des tables dédiées ; `source_pages` compte les pages
de la base d’origine et `cleaned_pages` les pages conservées après le filtre.
La base préparée conserve aussi, quand elles existent, la langue et la
description publiées dans le HTML, les mots-clés déclarés par la page, les
titres structurants (`h1` à `h6`), un type de page déduit des segments d’URL
et le nombre de mots du texte nettoyé. Ces champs sont des métadonnées de
provenance, pas des résumés générés ; les mots-clés et descriptions absents
restent vides. Si `clean_crawl.db` existe déjà, les nouvelles URL sont ajoutées
sans écraser les pages existantes ; relancer le cleaner sur les mêmes URL
n’ajoute pas de doublons. Pour remplacer volontairement toute la base, choisis
un nouveau chemin de sortie ou retire explicitement l’ancienne base après
vérification.

La sortie doit rester différente de la base source.

### Indexer et rechercher les PDF

Créer un index (la base de sortie ne doit pas déjà exister) :

```powershell
.\.venv\Scripts\python.exe pdf_index.py index
```

Personnaliser les chemins :

```powershell
.\.venv\Scripts\python.exe pdf_index.py index --source-db .\crawl.db --index-db .\pdf-index.db
```

Rechercher une expression :

```powershell
.\.venv\Scripts\python.exe pdf_index.py search "recrutement concours"
```

Les mots de la recherche sont combinés avec `AND` : les résultats doivent
correspondre à tous les mots recherchés. Modifier la base utilisée ou le nombre
maximal de résultats :

```powershell
.\.venv\Scripts\python.exe pdf_index.py search "budget national" --index-db .\pdf-index.db --limit 10
```

Les documents sont marqués `indexed` s’ils ont été traités, `no_text` si aucun
texte n’a pu être extrait, ou `error` si une erreur de lecture est survenue.
Les PDF numérisés comme images peuvent nécessiter une étape OCR séparée ; l’OCR
n’est pas fourni par ce prototype.

### Démarrer l’API

```powershell
.\.venv\Scripts\python.exe api.py
```

Voir [API.md](./API.md) pour le guide API. Les paramètres suivants peuvent être
définis dans PowerShell avant le démarrage :

```powershell
$env:OPENDATA_PAGES_DB = "C:\data\clean_crawl.db"
$env:OPENDATA_PDF_DB = "C:\data\pdf-index.db"
$env:OPENDATA_CRAWL_DB = "C:\data\crawl.db"
$env:OPENDATA_IMAGES_DIR = "C:\data\img"
$env:OPENDATA_HOST = "127.0.0.1"
$env:OPENDATA_PORT = "8000"
.\.venv\Scripts\python.exe api.py
```

Si ces variables ne sont pas définies, l’API utilise `clean_crawl.db`,
`pdf-index.db`, `crawl.db` et `img/` dans le dossier du projet. La galerie
utilise uniquement les images téléchargées présentes dans `img/`, référencées
par la base du crawler ; elle ne récupère jamais une image depuis son site
d’origine. Pour supprimer les variables de la session PowerShell :

```powershell
Remove-Item Env:OPENDATA_PAGES_DB, Env:OPENDATA_PDF_DB, Env:OPENDATA_CRAWL_DB, Env:OPENDATA_IMAGES_DIR, Env:OPENDATA_HOST, Env:OPENDATA_PORT -ErrorAction SilentlyContinue
```

## API et tableau de bord

Le service est configuré pour écouter sur `127.0.0.1:8000` par défaut.

| Méthode | Route | Description |
| --- | --- | --- |
| `GET` | `/` | Tableau de bord de recherche. |
| `GET` | `/api/v1/health` | État du service et disponibilité des bases. |
| `GET` | `/api/v1/sites` | Sites disponibles dans les bases préparées. |
| `GET` | `/api/v1/pages` | Liste et recherche des pages (`match_mode=contains` ou `whole`). |
| `GET` | `/api/v1/pages/{id}` | Détail d’une page. |
| `GET` | `/api/v1/documents` | Liste et recherche des PDF indexés (`match_mode=contains` ou `whole`). |
| `GET` | `/api/v1/documents/{id}` | Détail d’un PDF indexé. |
| `GET` | `/api/v1/images` | Galerie paginée des images locales (`q`, `site`, `match_mode`, `limit`, `offset`). |
| `GET` | `/api/v1/images/file?url={url}` | Sert une image enregistrée en base et confinée à `img/`. |
| `GET` | `/docs` | Documentation OpenAPI interactive de FastAPI. |

Exemples de requêtes :

```text
http://127.0.0.1:8000/api/v1/health
http://127.0.0.1:8000/api/v1/sites
http://127.0.0.1:8000/api/v1/pages?q=budget&limit=20&offset=0
http://127.0.0.1:8000/api/v1/pages?q=budget&site=primature.gov.bf&match_mode=whole
http://127.0.0.1:8000/api/v1/documents?q=recrutement&limit=10&match_mode=contains
http://127.0.0.1:8000/api/v1/images?site=primature.gov.bf&limit=20&match_mode=contains
```

Les paramètres `q` et `site` filtrent les pages et les documents. `match_mode`
choisit entre `contains` (par défaut, trouve aussi une partie de mot, par
exemple `char` dans `charge`) et `whole` (mot entier uniquement). Dans les
recherches textuelles, les termes sont combinés avec `AND`. Les listes sont
limitées par défaut à 20 éléments, avec un maximum de 100 par requête ; `offset`
permet de parcourir les pages suivantes. Le texte des fiches détaillées est
plafonné à 100 000 caractères. Les liens des PDF renvoient vers les URL des
sources institutionnelles ; l’API ne télécharge ni ne sert directement les
fichiers PDF.

Les réponses des routes de liste exposent les champs utilisés dans
`search_fields` (`title` et `text` pour les pages ; `title`, `author`, `site`
et `text` pour les PDF) ainsi que les termes analysés dans `matched_terms`.
Chaque résultat comprend son titre, son URL de source et un extrait de texte.
L’extrait d’une page est centré sur la première occurrence du premier terme
recherché ; pour les PDF, l’extrait provient de l’index plein texte. Le tableau
de bord met en évidence les termes recherchés dans les titres, les extraits et
le texte intégral. Cliquer sur **Lire le texte intégral** récupère le texte
complet depuis la base SQLite locale ; les routes de détail ne le tronquent
pas. L’URL d’origine est affichée comme provenance, sans lien externe : ouvrir
un résultat n’effectue aucune requête au site institutionnel. Les PDF
eux-mêmes ne sont pas servis ; seul le texte extrait et enregistré dans
l’index local est affiché.

Une base préparée manquante peut conduire à un état dégradé ou à une réponse
`503`. Une fiche qui n’existe pas renvoie `404` ; des paramètres invalides
renvoient `422`.

## Fichiers produits

| Chemin par défaut | Rôle |
| --- | --- |
| `crawl.db` | Base source : file d’URL, pages collectées, images et documents. |
| `crawl.db-wal`, `crawl.db-shm` | Fichiers SQLite temporaires possibles pendant l’utilisation de la base. |
| `img/` | Images téléchargées pendant le crawl. |
| `documents/` | Documents téléchargés pendant le crawl. |
| `crawler.log.jsonl` | Journaux du crawler au format JSON Lines. |
| `clean_crawl.db` | Texte des pages préparé et rapports de nettoyage. |
| `pdf-index.db` | Métadonnées, texte extrait et index de recherche plein texte PDF. |
| `crawl-export.xlsx` | Classeur optionnel, créé avec `--export-excel`. |

Les bases, leurs éventuels fichiers WAL/SHM, les médias téléchargés et les
exports sont des données générées ; ils ne sont pas destinés à être ajoutés au
dépôt. Ne pas supprimer les fichiers `-wal` ou `-shm` pendant qu’un processus
utilise la base SQLite correspondante.

## Tests

Depuis la racine du dépôt, après installation de `requirements-dev.txt` :

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Pour ne lancer que les tests de l’API :

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_public_api.py -q
```

Les tests utilisent des données temporaires et ne nécessitent pas de lancer le
serveur web. Un avertissement pytest peut signaler que `TestHandler` dans les
tests du crawler n’est pas une classe de test ; cet avertissement ne signifie
pas à lui seul qu’un test a échoué.

## Droits, provenance et précautions

- La présence d’un contenu sur un site institutionnel ne suffit pas à prouver
  qu’il est librement réutilisable.
- Le projet ne déduit ni licence, ni domaine public, ni autorisation de
  republication. Le statut est explicitement `not_assessed`.
- Les URL d’origine et certaines informations de site sont conservées afin de
  rendre la provenance vérifiable.
- Avant toute publication ou réutilisation, vérifier les licences, mentions
  légales, conditions d’utilisation, droits sur les textes, images et PDF,
  ainsi que les éventuelles données personnelles. Demander une autorisation
  lorsqu’elle est nécessaire.
- Respecter les règles d’accès des sites, leurs limites de débit et toute
  demande de retrait ou restriction applicable.
- L’API est un prototype de consultation, sans authentification ni préparation
  complète à une exposition publique. **Ne pas la rendre accessible depuis
  Internet en l’état.** Un déploiement public nécessite notamment une revue de
  sécurité et juridique, des contrôles d’accès adaptés, de la limitation de
  débit, de la surveillance et une décision explicite sur les données
  publiables.

## Structure du projet

```text
.
├── api.py                  # Application FastAPI et tableau de bord
├── API.md                  # Documentation de l’API
├── cli.py                  # Entrée CLI du crawler et export Excel
├── cleaner.py              # CLI de nettoyage des pages
├── config.yaml             # Sites et paramètres YAML du crawl
├── crawler/
│   ├── cleaner.py          # Nettoyage HTML et génération de la base texte
│   ├── database.py         # Stockage et file d’URL SQLite
│   ├── engine.py           # Configuration et orchestration du crawler
│   ├── excel_export.py     # Export Excel
│   ├── fetcher.py          # Requêtes HTTP
│   ├── file_store.py       # Téléchargement et stockage des fichiers
│   ├── parser.py           # Extraction des données des pages
│   ├── pdf_index.py        # Extraction et recherche de PDF
│   ├── public_api.py       # Requêtes en lecture seule pour l’API
│   └── scheduler.py        # File partagée, workers et suivi des tâches
├── pdf_index.py            # CLI d’indexation et de recherche PDF
├── requirements.txt        # Dépendances d’exécution
├── requirements-dev.txt    # Dépendances de test
└── tests/                  # Tests automatisés
```

## Dépannage

### Le tableau de bord indique que les données sont indisponibles

Vérifier que `clean_crawl.db` et `pdf-index.db` existent. Si nécessaire,
exécuter les commandes de nettoyage et d’indexation depuis une base `crawl.db`
déjà alimentée. Vérifier également les valeurs de `OPENDATA_PAGES_DB` et
`OPENDATA_PDF_DB` dans la session PowerShell courante.

### Le cleaner ou l’indexeur indique que la sortie existe déjà

Ces commandes refusent de remplacer une base existante. Choisir un autre nom
de sortie ou déplacer l’ancienne base après avoir vérifié qu’elle n’est pas
utilisée. Ne jamais utiliser le même chemin pour la base source et la sortie.

### Un PDF apparaît avec le statut `no_text`

Le PDF peut être vide, contenir uniquement des pages numérisées, ou ne pas
exposer de texte extractible. L’indexeur courant n’effectue pas d’OCR.

### Le crawler est interrompu ou le réseau échoue

Consulter `crawler.log.jsonl` et relancer le crawler avec la même configuration
pour reprendre les URLs restantes. Ajuster ponctuellement `--workers`,
`--retries` ou `--max-pages` si cela est approprié au site et à l’objectif de
collecte.

### Une base SQLite semble accompagnée de fichiers `-wal` ou `-shm`

Ces fichiers peuvent être normaux pendant l’activité SQLite en mode WAL.
Laisser le processus terminer et fermer proprement la base avant tout
déplacement ou archivage.

---

*Fait avec ❤️ au Burkina Faso, en soutien à la révolution progressiste populaire. *
