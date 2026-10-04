# L2C Review — vérification des dessins d'atelier d'armature

Outil local, en Python, qui lit les **plans de structure L2C** et les **dessins
d'atelier (DA)** du fabricant d'armature, en extrait chaque élément armé avec son
feuillet et sa position X/Y, produit une base JSON conforme à l'annexe A des consignes,
puis compare les deux côtés pour faire ressortir les écarts à réviser.

Tout s'exécute sur le poste : aucun service infonuagique, aucune API d'IA externe.

| Document | Contenu |
|---|---|
| **README.md** (ce fichier) | architecture, installation, exécution, hypothèses, limites |
| [PLAN.md](PLAN.md) | document de conception : mesures sur le corpus, choix d'outils, risques |
| [DA_PLAN.md](DA_PLAN.md) | plan de travail et journal d'avancement du côté dessins d'atelier |
| [src/l2c/da/parsers/README.md](src/l2c/da/parsers/README.md) | stratégie des parseurs DA et contrats de sortie JSON |

## État d'avancement

| | Volet | Portée |
|---|---|---|
| ✅ | Extraction côté **plan** | Les six types d'éléments (radier, semelle, poutre, mur de refend, colonne, dalle), sur les quatre projets de développement |
| ✅ | Localisation | Grille lue à partir des **lignes** d'axe dessinées (`K-6`, `J-10.8`, `B.2-35`), correcte sur les feuillets à plusieurs vues ; murs par élévation (`élévation B - RDC @ 2`), poutres par repère (`P108`) |
| ✅ | Base **JSON** annexe A | Validée par Pydantic, déterministe, identifiants uniques, manifeste d'exécution |
| ✅ | Clé de réponse CLP | 6/6 lignes de `CLP_dismatch.xlsx` retrouvées côté plan (`l2c truth`) |
| ✅ | **Tableau de bord** web | Choisir un projet → extraire → inspecter → télécharger |
| 🟡 | Extraction côté **DA** | **CLP seulement** : colonnes, dalles, semelles, poutres, radiers, lus par OCR sur l'image de la page |
| 🟡 | **Comparaison** plan ↔ DA | CLP, cinq types ; c'est une comparaison de lectures, pas un verdict de conformité certifié |
| ⬜ | DA des autres projets | WP2, LIGREP, EspCa3B : non branchés au tableau de bord |
| ⬜ | Murs de refend côté DA | Aucun lecteur |
| ⬜ | Rapport PDF | Non réalisé (`src/l2c/report/` est vide) |

## Architecture

```
  dossier d'un projet
  ├── L2C_PLAN_STR_<projet>.pdf ──► page.py ──► parse/<type>.py ──► ElementRecord (source="plan")
  │      (PDF vectoriel,             coordonnées      + geometry/                     │
  │       couche texte)              normalisées      (grille, symboles)              │
  │                                                                                   ▼
  └── DA/<type>/*.pdf ──► da/parsers/<type>_clp.py ──► ElementRecord (source="atelier")
         (lus comme des       rendu en image + OCR                                    │
          images)             (RapidOCR / ONNX, CPU)                                  ▼
                                                                    record_formats.py  (même sens des
                                                                    champs des deux côtés)
                                                                                      │
                                              ┌───────────────────────────────────────┤
                                              ▼                                       ▼
                                   io_json.py : elements_plan.json,          comparison.py :
                                   elements_atelier.json, run_manifest.json  comparaison.json
```

### Modules

| Chemin | Rôle |
|---|---|
| `src/l2c/page.py` | Préparation des pages. Corrige une fois pour toutes les deux pièges de coordonnées (origine de MediaBox non nulle, `/Rotate 90`) et lit le numéro et le type de feuillet. Seul module autorisé à appeler `get_text` / `get_drawings`. |
| `src/l2c/pipeline.py` | Orchestration côté plan : classe chaque feuillet et l'envoie au parseur de son type. Les feuillets sans armature sont rapportés `skipped` avec la raison. |
| `src/l2c/parse/` | Un parseur de plan par type d'élément (`columns`, `footings`, `radier`, `beams`, `walls`, `slabs`, `slab_integrity`). |
| `src/l2c/geometry/` | Grille d'axes à partir des lignes (`gridlines.py`), convention d'axes par projet (`grid.py`), détection des symboles et auto-calibration de l'échelle (`symbols.py`). |
| `src/l2c/units.py` | Unique point de conversion impérial/métrique vers les millimètres ; vocabulaire fermé des barres (`10M` … `55M`). |
| `src/l2c/model.py` | `Armature` / `ElementRecord` : le schéma de l'annexe A, validé par Pydantic. Le matériel de débogage est exclu de la sérialisation. |
| `src/l2c/io_json.py` | Écriture et validation du JSON, manifeste d'exécution. |
| `src/l2c/da/parsers/` | Lecteurs DA par type et par fabricant (`colonne_clp`, `dalle_clp`, `semelle_clp`, `poutre_clp`, `radier_clp`), chacun exécutable seul ; `output.py` déduplique les observations répétées. |
| `src/l2c/da/imageread.py` | Lecture d'une page **comme image** : détection par tuiles, puis reconnaissance OCR de chaque ligne. |
| `src/l2c/da/dashboard.py` | Lanceur DA du tableau de bord : fichiers d'entrée, points de reprise par fichier et par page. |
| `src/l2c/da/jobs.py` | Tâches DA dans un processus séparé, registre durable sous `.cache/da_jobs/`. |
| `src/l2c/da/{pipeline,columns,planview,beams,decode,glyphs,vision,inventory}.py` | Lecteurs antérieurs (couche texte, décodeur de glyphes vectoriels), conservés comme référence mesurée et pour `l2c truth`. Ils n'alimentent pas le tableau de bord. |
| `src/l2c/record_formats.py`, `beam_records.py`, `column_records.py` | Mise en forme commune plan/DA : mêmes rôles, mêmes alias de niveau, mêmes champs. Normalise la représentation seulement, sans jamais combler une valeur manquante. |
| `src/l2c/comparison.py` | Appariement et comparaison des enregistrements des deux côtés. |
| `src/l2c/answer_key.py` | Vérification contre `*_dismatch.xlsx`, lu à l'exécution, jamais copié dans le code. |
| `src/l2c/cli.py` | Commande `l2c` (`run`, `validate`, `truth`). |
| `app/` | Tableau de bord Streamlit ; `views.py` contient les composants communs aux sections plan et DA. |
| `tests/` | Suite pytest, exécutée contre le corpus réel. |
| `notebooks/` | Exploration du corpus et démonstration complète sur les colonnes de CLP. |

### Principes de conception

- **Le lecteur DA lit à l'aveugle.** Il ne voit jamais la valeur attendue du plan ; il
  produit des enregistrements, et la comparaison est du code ordinaire, dans une étape
  séparée. Sinon un lecteur « guidé » par le plan masquerait précisément les écarts
  recherchés.
- **Vocabulaire fermé.** Une désignation de barre hors de `10M`…`55M` est refusée par le
  modèle : une confusion `M`→`H` ne peut pas être sérialisée.
- **Rien n'est masqué.** Un feuillet ou un fichier non lu est listé avec sa raison ; il
  ne compte jamais pour « zéro élément ». Une lecture partielle garde les valeurs
  connues et signale ce qui manque.
- **Détection plutôt que suppression.** En cas de doute (appariement ambigu, élément sans
  vis-à-vis, direction non résolue), l'écart reste affiché avec le statut « à réviser »
  et ses sources. Seules les observations répétées et prouvées identiques sont
  dédupliquées.
- **Déterminisme.** Deux exécutions sur les mêmes fichiers donnent le même JSON, à
  l'octet près côté plan.

## Installation

Prérequis : **Python ≥ 3.10** (développé sous 3.13), environ 1 Go d'espace disque pour
l'environnement, aucun GPU requis.

### Linux / macOS

```bash
make install                                # crée .venv, installe le cœur + tableau de bord + tests
.venv/bin/pip install -e '.[da,notebook]'   # OCR des dessins d'atelier et notebook
make doctor                                 # vérifie l'environnement et la présence du corpus
```

`make install` n'installe **pas** l'extra `da` : sans lui, la section « Dessins
d'atelier » et les parseurs DA ne fonctionnent pas.

### Windows

Nécessite [uv](https://docs.astral.sh/uv/).

```bat
install.cmd      :: crée .venv (Python 3.13) et installe tout : tableau de bord, OCR, tests, notebook
```

Voir la limite Windows du tableau de bord dans [Limites connues](#limites-connues).

### Extras

| Extra | Contenu | Requis pour |
|---|---|---|
| *(cœur)* | pymupdf, numpy, scipy, pillow, pydantic, reportlab, openpyxl | extraction côté plan, CLI |
| `app` | streamlit | tableau de bord |
| `da` | rapidocr, onnxruntime, opencv-python-headless | lecture des dessins d'atelier |
| `dev` | pytest, pdfplumber (contre-vérification dans les tests seulement) | tests |
| `notebook` | notebook, nbconvert, matplotlib, pandas | notebook |

`requirements.txt` fige les versions exactes d'une installation de référence
(`make freeze`).

### Corpus

Le corpus est confidentiel et **n'est pas dans le dépôt**. Emplacement par défaut :
`~/Downloads/l2c-participants/`, avec un dossier par projet :

```
l2c-participants/
└── CLP/
    ├── L2C_PLAN_STR_CLP.pdf      plan de structure
    ├── CLP_dismatch.xlsx         clé de réponse (CLP seulement)
    └── DA/
        ├── Colonnes/  Dalles/  Fondations/  Poutres/
```

Pour un autre emplacement : variable d'environnement `L2C_CORPUS` (tests, notebook),
`make … CORPUS=/chemin`, ou simplement le chemin passé en argument à `l2c`.

## Exécution

### Tableau de bord

```bash
make ui                 # http://localhost:8501
make ui PORT=8520
```

Dans la barre latérale, indiquer le dossier d'un projet (ou du corpus, puis choisir le
projet), ou téléverser l'archive ZIP d'un projet. Trois sections :

1. **Plan L2C** — extraction de tous les types, tableaux, diagnostics par feuillet,
   téléchargement de `elements_plan.json`.
2. **Dessins d'atelier** — lance la lecture OCR des DA dans un processus d'arrière-plan.
   On peut changer de section ou rafraîchir la page : la tâche continue. Chaque PDF
   terminé est sauvegardé ; après un arrêt, **Reprendre la génération** ne relit que les
   fichiers manquants. Téléchargement de `elements_atelier.json`.
3. **Comparaison** — disponible une fois les deux côtés chargés ; ne lance jamais de
   lecture. Filtres par type et par niveau, sources de chaque côté, téléchargement de
   `comparaison.json`.

La lecture complète des DA de CLP (onze fichiers) prend plusieurs dizaines de minutes
sur un processeur de portable ; les résultats sont ensuite repris du cache
(`.cache/da_jobs/`), invalidé si un fichier source ou la version du parseur change.

Statuts de la comparaison :

| Statut | Signification |
|---|---|
| `same` | armatures extraites identiques |
| `changed` | armatures différentes ou annotations supplémentaires |
| `missing_da` | élément présent au plan seulement |
| `missing_plan` | élément présent dans les DA seulement |
| `review` | localisation, niveau, couche, direction ou rôle non résolu : à vérifier |
| `out_of_scope` | type, niveau ou couche absent des résultats DA chargés |

### Ligne de commande

```bash
make run PROJECT=WP2          # un projet -> out/WP2/elements_plan.json + run_manifest.json
make run-all                  # les quatre projets
make validate-all             # valide chaque JSON contre l'annexe A
make summary                  # une ligne de résumé par projet
make truth                    # clé de réponse CLP
make clean                    # supprime out/ et les caches
make purge                    # supprime aussi toute copie du corpus sous le dépôt
```

`make` seul liste toutes les cibles. Commandes équivalentes sans `make` :

```bash
.venv/bin/l2c run ~/Downloads/l2c-participants/CLP --out out
.venv/bin/l2c validate out/CLP/elements_plan.json
.venv/bin/l2c truth ~/Downloads/l2c-participants/CLP
```

Sous Windows : `.venv\Scripts\l2c.exe run %USERPROFILE%\Downloads\l2c-participants\CLP --out out`.

### Parseurs DA en autonome

Chaque parseur s'exécute seul, écrit son JSON de révision et, avec `--annotated`, un PDF
annoté pour contrôle visuel. `--check` utilise la couche texte cachée du PDF **après**
la lecture d'image, uniquement pour valider.

```bash
export PYTHONPATH=src
.venv/bin/python -m l2c.da.parsers.semelle_clp --check --annotated out/semelle_clp_review.pdf
.venv/bin/python -m l2c.da.parsers.dalle_clp   --coordinate J-15 --check --annotated out/dalle_clp_review.pdf
.venv/bin/python -m l2c.da.parsers.poutre_clp  --check --annotated out/poutre_clp_review.pdf \
    --compare-plan "$HOME/Downloads/l2c-participants/CLP/L2C_PLAN_STR_CLP.pdf"
```

Fichiers lus pour CLP, relativement au dossier du projet :

| Type | Fichier | Pages lues |
|---|---|---|
| Colonnes | `DA/Colonnes/CLP_COLONNES Partie 3.pdf` + page 5 de `Partie 1` (sous-sol) | toutes |
| Dalles | chaque PDF de `DA/Dalles/` (bas, haut, acier d'intégrité) | toutes |
| Radiers | `DA/Fondations/CLP_RADIERS.pdf` | toutes |
| Semelles | `DA/Fondations/CLP_SEMELLES FND.pdf` | dernière |
| Poutres | `DA/Poutres/CLP_POUTRES.pdf` | dernière |

Les fichiers produits par chaque parseur et leur format sont décrits dans
[src/l2c/da/parsers/README.md](src/l2c/da/parsers/README.md).

### Notebook

`notebooks/exploration_and_colonnes_clp_demo.ipynb` explore le corpus (quels PDF ont une
couche texte, que contiennent les autres, les trois lecteurs essayés), puis déroule le
pipeline complet des colonnes de CLP (plan → DA → enregistrements communs →
comparaison → JSON) en dessinant chaque étape sur le feuillet.

```bash
.venv/bin/jupyter notebook notebooks/exploration_and_colonnes_clp_demo.ipynb
```

La première fois, *Run All* prend environ 25 minutes sur un portable : l'OCR des
colonnes du DA en prend 15 à 20. Le résultat de l'OCR est gardé dans
`out/notebook_colonnes_clp/`, et les fois suivantes le notebook tourne en 5 minutes
environ. Le notebook se commite **sans sorties** : ses figures sont des extraits de
dessins confidentiels.

### Sorties

| Fichier | Contenu |
|---|---|
| `out/<projet>/elements_plan.json` | enregistrements annexe A, côté plan |
| `out/<projet>/run_manifest.json` | version du pipeline, système d'unités détecté, état de chaque feuillet |
| `<projet>_elements_atelier.json` | enregistrements annexe A, côté DA (téléchargement du tableau de bord) |
| `<projet>_comparaison.json` | toutes les lignes de comparaison, avec statut, raison et sources |

Les coordonnées `x`, `y` sont en points PDF, origine en haut à gauche de la page telle
qu'affichée (rotation retirée). Les longueurs et espacements sont en millimètres.

## Résultats côté plan

```
PROJET   FEUILLETS  ENREG.  radier semelle poutre mur_refend colonne dalle  UNITÉS
CLP          18      2468      77      75    187        119     395  1615  impérial
WP2          33      4681      35     124    283        212     912  3115  métrique
LIGREP       26      3562      30     112    240        174     677  2329  métrique
EspCa3B      45      2806      54      20    212        369     644  1507  métrique
```

`FEUILLETS` compte les feuillets portant de l'armature d'élément ; `make summary`
régénère ces chiffres. Un enregistrement localisé n'est pas pour autant prouvé exact.
Les contrôles qui le sont :

- **Clé de réponse** — les 6 lignes de `CLP_dismatch.xlsx` sont retrouvées à partir des
  dessins (radier `J-10.8`, semelle `L-13`, mur `élévation B - RDC @ 2`, colonnes `K-6`
  et `I-13`, dalle `J-15`).
- **Géométrie de grille** — là où la grille par lignes et l'ancienne grille par
  étiquettes divergeaient, la première place le symbole à 0,0–0,6 pt de ses axes, la
  seconde à 20–326 pt.

Côté DA et comparaison (CLP) : les poutres donnent 27 poutres / 187 armatures au plan
contre 20 poutres / 162 armatures dans le DA lu ; pour les semelles, la différence
connue en `L-13` ressort comme `changed`.

## Tests

```bash
make test-coords     # pièges de coordonnées
make test-e2e        # exécutions complètes sur le corpus
make test-ui         # tableau de bord, sans navigateur
.venv/bin/python -m pytest tests/test_semelle_clp.py -q     # un fichier à la fois
```

Les tests lisent le corpus réel (`L2C_CORPUS`) et sont sautés proprement s'il est
absent. **`make test` lance toute la suite, y compris `tests/test_app.py`, qui démarre
une vraie lecture OCR des DA de CLP** (très long) : préférer les fichiers ciblés.

| Fichier | Ce qu'il garde |
|---|---|
| `test_coords.py` | origine de MediaBox non nulle (19 % des pages) et `/Rotate 90` (65 %) |
| `test_end_to_end.py` | volumes par projet et par type, identifiants uniques, unités, schéma, déterminisme, clé de réponse |
| `test_sheets.py` | numéro de feuillet unique par page, type cohérent avec la série |
| `test_model.py`, `test_units.py` | vocabulaire fermé des barres, conversions |
| `test_*_clp.py`, `test_da*.py` | parseurs DA, tâches d'arrière-plan et points de reprise |
| `test_comparison*.py`, `test_*_records.py`, `test_record_formats.py` | format commun et comparaison |
| `test_app*.py` | tableau de bord piloté par `streamlit.testing` |

## Hypothèses

**Sur les documents**

- Un projet est un dossier contenant un plan nommé `L2C_PLAN_STR_*.pdf` et un
  sous-dossier `DA/`.
- Les plans L2C sont des PDF **vectoriels avec couche texte**. Un plan numérisé n'est
  pas lu.
- Le type d'un feuillet se déduit de son titre et de sa série (S-050/060 radier, S-100
  semelles, S-300 poutres, S-400 murs de refend, S-500 colonnes, S-600 dalles).
- Les axes sont dessinés comme des lignes longues (≥ 150 pt) terminées par une bulle
  étiquetée. La convention lettres/chiffres est détectée par projet, mais le
  localisateur émis est toujours `<lettre>-<chiffre>`.
- Les barres suivent la désignation canadienne (`10M` à `55M`).
- Le système d'unités (impérial ou métrique) est détecté par projet, jamais supposé ;
  la sortie est toujours en millimètres.

**Sur la lecture des dessins d'atelier**

- Les DA sont lus comme des **images**, indépendamment de la façon dont le logiciel du
  fabricant encode le texte (couche texte, glyphes vectorisés ou numérisation).
- Chaque parseur DA suppose la **mise en page d'un fabricant** : emplacement des
  tableaux, étiquettes de rôle (`VERT:`, `ÉTRI:`, `LONG:`, `TRAN:`), symboles. Ceux
  livrés sont calés sur CLP.
- Pour les colonnes de CLP, `Partie 3` remplace les quatre premières pages de
  `Partie 1` ; aucune résolution générale des révisions n'est faite.

**Sur la comparaison**

- Deux enregistrements sont appariés par type, niveau normalisé, couche de dalle et
  identifiant (coordonnée de grille ou repère).
- On compare la spécification principale (quantité, diamètre, espacement). Les repères
  de façonnage, longueurs de coupe et nombres de pièces du fabricant restent dans les
  éléments de preuve et ne déclenchent pas d'écart.

## Limites connues

**Portée**

- **Seuls les DA de CLP sont lus.** Pour WP2, LIGREP et EspCa3B, le tableau de bord
  affiche un message d'indisponibilité. Un projet inconnu sera donc extrait côté plan,
  mais pas comparé. Un lecteur de semelles pour EspCa3B existe à l'état expérimental
  (`src/l2c/da/parsers/semelle_espca3b.py`), non branché.
- **Aucun lecteur DA pour les murs de refend.**
- **Pas de rapport PDF**, ni de classement final `conforme` / `non conforme` /
  `manquant` / `ajouté` : la section Comparaison montre des écarts de lecture à faire
  valider par une personne.
- Semelles et poutres : seule la dernière page du PDF est lue. Sept élévations de
  poutres du plan n'ont pas de vis-à-vis dans le DA lu.
- Les chemins des fichiers DA de CLP sont fixés dans `l2c.da.dashboard` ; le
  téléversement direct de PDF de DA n'est pas offert dans l'interface.

**Exactitude**

- Seul CLP a une clé de réponse. Sur les trois autres projets, l'exactitude repose sur
  des contrôles géométriques, pas sur une vérité étiquetée.
- Dalles : un appel placé entre deux colonnes est rattaché à la plus proche (la
  confiance reflète la distance). Les appels denses restent à réviser.
- Poutres : l'accord de deux armatures exige encore une vérification de leur position
  le long de la poutre (P112 et P116 notamment).
- Murs : dans LIGREP, S-400 et S-401 nomment tous deux leurs vues A, B, C ; un mur n'est
  unique qu'avec son feuillet.
- Radiers de WP2 et d'EspCa3B : le rang est déduit de la légende de direction de la vue
  (`RANG 1 & 4` / `RANG 2 & 3`), à confiance réduite.
- Colonnes sans symbole correspondant dans un rayon de 100 pt (CLP 3, WP2 11) : placées
  d'après la position de l'appel, à confiance réduite.
- Quelques colonnes partagent un localisateur (WP2 S-512 `T.1-34..37`, EspCa3B `B-2`).
- Les tableaux de légende et de détails (espacement des épingles, `ARM. ADD.`, détail
  de pilastre) ne sont pas rattachés à la grille et ne sont pas extraits.
- Les erreurs d'OCR résiduelles sont signalées par le score de confiance et le statut
  `review`, pas corrigées.

**Technique**

- **Tableau de bord sous Windows.** `src/l2c/da/jobs.py` importe `fcntl`, absent de
  Python pour Windows ; `ui.cmd` échoue donc au démarrage sur un Windows natif. La
  ligne de commande, les parseurs DA autonomes et le notebook y fonctionnent. Utiliser
  Linux, macOS ou WSL pour le tableau de bord.
- La lecture OCR est lente sur processeur seul (plusieurs dizaines de minutes pour
  CLP) ; il n'y a pas d'accélération GPU.
- Le cache des tâches DA (`.cache/da_jobs/`) contient des fichiers `pickle` : ne charger
  que ceux produits localement.
- `make` suppose un environnement POSIX ; sous Windows, utiliser `install.cmd` et les
  commandes directes.

## Confidentialité et licences

- Le corpus est confidentiel (consignes §4). `.gitignore` exclut les plans, les
  dossiers `DA/`, la clé de réponse, `out/`, les caches et les images. Le chemin du
  corpus est toujours un argument, jamais inscrit dans le code. `make purge` supprime
  les sorties et toute copie du corpus sous le dépôt ; le corpus lui-même doit être
  supprimé du poste après l'événement.
- Aucune donnée ne quitte le poste. Les modèles OCR (poids ONNX de RapidOCR) sont des
  fichiers locaux ; s'ils sont absents, RapidOCR les télécharge une fois, à la première
  utilisation — prévoir cette étape avant de travailler hors ligne.
- Toutes les dépendances sont à code source ouvert. **PyMuPDF est sous AGPL-3.0** : sans
  conséquence pour un usage local, mais à considérer si l'outil devait être offert
  comme service réseau (voir PLAN.md §13).
