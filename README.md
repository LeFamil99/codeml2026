# L2C Review — vérification des dessins d'atelier d'armature

## Livrables

Les livrables se trouvent à la racine du projet :

| Fichier | Contenu |
|---|---|
| `WP2_elements_plan.json` | Éléments armés extraits du plan L2C du projet WP2 (annexe A) |
| `LIGREP_elements_plan.json` | Éléments armés extraits du plan L2C du projet LIGREP (annexe A) |
| `EspCa3B_elements_plan.json` | Éléments armés extraits du plan L2C du projet EspCa3B (annexe A) |
| `CLP_comparaison.json` | Comparaison plan / dessins d'atelier du projet CLP, avec statut, motif et sources de chaque élément |
| `CLP_rapport_comparaison.pdf` | Rapport PDF de la comparaison CLP |

Le notebook de démonstration est dans le dossier `notebooks/` :
`notebooks/exploration_and_colonnes_clp_demo.ipynb` (exploration et lecture des colonnes CLP).

## Présentation

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
| ✅ | Extraction côté **plan** | Les six types d'éléments (radier, semelle, poutre, mur de refend, colonne, dalle), sur les quatre projets de développement ; pour les dalles, seul l'acier d'intégrité est extrait |
| ✅ | Localisation | Grille lue à partir des **lignes** d'axe dessinées (`K-6`, `J-10.8`, `B.2-35`), correcte sur les feuillets à plusieurs vues ; murs par élévation (`élévation B - RDC @ 2`), poutres par repère (`P108`) |
| ✅ | Base **JSON** annexe A | Validée par Pydantic, déterministe, identifiants uniques, manifeste d'exécution |
| 🟡 | Clé de réponse CLP | 5/6 lignes de `CLP_dismatch.xlsx` retrouvées côté plan (`l2c truth`) ; la ligne de dalle `J-15` ne l'est plus |
| ✅ | **Tableau de bord** web | Téléverser le plan et les DA → extraire → inspecter → télécharger |
| 🟡 | Extraction côté **DA** | Une seule méthode d'analyse, celle des **formats CLP** : colonnes, dalles, semelles, poutres, radiers, lus par OCR sur l'image de la page |
| 🟡 | **Comparaison** plan ↔ DA | Cinq types ; liste les éléments lus des deux côtés dont les armatures diffèrent. C'est une comparaison de lectures, pas un verdict de conformité certifié |
| ✅ | **Rapport PDF** | Rapport de comparaison généré par ReportLab, téléchargeable depuis la section Comparaison |
| ⬜ | DA des autres fabricants | WP2, LIGREP, EspCa3B : aucune méthode d'analyse enregistrée |
| ⬜ | Murs de refend côté DA | Aucun lecteur |

## Architecture

```
  fichiers d'un projet
  ├── L2C_PLAN_STR_<projet>.pdf ──► page.py ──► parse/<type>.py ──► ElementRecord (source="plan")
  │      (PDF vectoriel,             coordonnées      + geometry/                     │
  │       couche texte)              normalisées      (grille, symboles)              │
  │                                                                                   ▼
  └── PDF de DA, par type ► da/parsers/<type>_clp.py ──► ElementRecord (source="atelier")
         (lus comme des       rendu en image + OCR                                    │
          images)             (RapidOCR / ONNX, CPU)                                  ▼
                                                                    record_formats.py  (même sens des
                                                                    champs des deux côtés)
                                                                                      │
                                              ┌───────────────────────────────────────┤
                                              ▼                                       ▼
                                   io_json.py : elements_plan.json,          comparison.py : comparaison.json
                                   elements_atelier.json, run_manifest.json  report/comparison_pdf.py : rapport PDF
```

### Modules

| Chemin | Rôle |
|---|---|
| `src/l2c/page.py` | Préparation des pages. Corrige une fois pour toutes les deux pièges de coordonnées (origine de MediaBox non nulle, `/Rotate 90`) et lit le numéro et le type de feuillet. Seul module autorisé à appeler `get_text` / `get_drawings`. |
| `src/l2c/pipeline.py` | Orchestration côté plan : classe chaque feuillet et l'envoie au parseur de son type. Les feuillets sans armature sont rapportés `skipped` avec la raison. |
| `src/l2c/parse/` | Un parseur de plan par type d'élément (`columns`, `footings`, `radier`, `beams`, `walls`, `slabs`, `slab_integrity`). Pour les dalles, seul l'acier d'intégrité (types encerclés, résolus par le tableau de détail du plan) est émis. |
| `src/l2c/geometry/` | Grille d'axes à partir des lignes (`gridlines.py`), convention d'axes par projet (`grid.py`), détection des symboles et auto-calibration de l'échelle (`symbols.py`). |
| `src/l2c/units.py` | Unique point de conversion impérial/métrique vers les millimètres ; vocabulaire fermé des barres (`10M` … `55M`). |
| `src/l2c/model.py` | `Armature` / `ElementRecord` : le schéma de l'annexe A, validé par Pydantic. Le matériel de débogage est exclu de la sérialisation. |
| `src/l2c/io_json.py` | Écriture et validation du JSON, manifeste d'exécution. |
| `src/l2c/da/parsers/` | Lecteurs DA par type et par fabricant (`colonne_clp`, `dalle_clp`, `semelle_clp`, `poutre_clp`, `radier_clp`), chacun exécutable seul ; `output.py` déduplique les observations répétées. |
| `src/l2c/da/imageread.py` | Lecture d'une page **comme image** : détection par tuiles, puis reconnaissance OCR de chaque ligne. |
| `src/l2c/da/methods.py` | Registre des **méthodes d'analyse** des DA. Une méthode déclare les types qu'elle lit et son lanceur ; seule `clp` existe. C'est le point d'extension pour un autre fabricant. |
| `src/l2c/da/dashboard.py` | Lanceur de la méthode CLP : fichiers d'entrée, points de reprise par fichier et par page, clé de cache par nom et contenu du fichier. |
| `src/l2c/da/jobs.py` | Tâches DA dans un processus séparé, registre durable sous `.cache/da_jobs/`. |
| `src/l2c/da/{pipeline,columns,planview,beams,decode,glyphs,vision,inventory}.py` | Lecteurs antérieurs (couche texte, décodeur de glyphes vectoriels), conservés comme référence mesurée et pour `l2c truth`. Ils n'alimentent pas le tableau de bord. |
| `src/l2c/record_formats.py`, `beam_records.py`, `column_records.py` | Mise en forme commune plan/DA : mêmes rôles, mêmes alias de niveau, mêmes champs. Normalise la représentation seulement, sans jamais combler une valeur manquante. |
| `src/l2c/comparison.py` | Appariement des enregistrements des deux côtés (étiquette identique, puis axes voisins) et liste des armatures différentes. |
| `src/l2c/report/comparison_pdf.py` | Rapport PDF de la comparaison (ReportLab). Lit les lignes de `comparison.py` et ne recalcule rien. |
| `src/l2c/answer_key.py` | Vérification contre `*_dismatch.xlsx`, lu à l'exécution, jamais copié dans le code. |
| `src/l2c/cli.py` | Commande `l2c` (`run`, `validate`, `truth`). |
| `app/` | Tableau de bord Streamlit. `views.py` : composants communs aux sections plan et DA ; `intake.py` : stockage des fichiers téléversés sous leur empreinte de contenu ; `theme.py` : apparence. |
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
- **Les sources restent attachées.** Chaque ligne de comparaison garde, pour les deux
  côtés, le fichier, le feuillet, la position et les annotations lues. Seules les
  observations répétées et prouvées identiques sont dédupliquées. Une valeur déduite
  plutôt que lue (par exemple l'ordre `NUM` / `ALP` de deux appels de dalle sans
  étiquette) est marquée comme telle.
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

Tout passe par le panneau de gauche, par téléversement :

1. **Plan L2C** — le PDF du plan. Le nom du projet est tiré du nom du fichier
   (`L2C_PLAN_STR_<projet>.pdf`).
2. **Méthode d'analyse** — la famille de dessins d'atelier à lire. Une seule est
   offerte : « CLP — formats d'atelier CLP ».
3. **Dessins d'atelier** — un champ par type lu par la méthode (colonnes, dalles,
   semelles, poutres, radiers), chacun acceptant plusieurs PDF. Un type laissé vide est
   simplement signalé comme sans fichier.

Les fichiers téléversés sont copiés sous `.cache/uploads/<projet>/<empreinte>/` ; un
même fichier retombe toujours au même endroit, d'une session à l'autre. La limite de
téléversement est de 1 Go (`.streamlit/config.toml`).

Trois sections :

1. **Plan L2C** — extraction de tous les types, tableaux, diagnostics par feuillet,
   téléchargement de `elements_plan.json`.
2. **Dessins d'atelier** — lance la lecture OCR des DA dans un processus d'arrière-plan.
   On peut changer de section ou rafraîchir la page : la tâche continue. Chaque PDF
   terminé est sauvegardé ; après un arrêt, **Reprendre la génération** ne relit que les
   fichiers manquants. Téléchargement de `elements_atelier.json`.
3. **Comparaison** — disponible une fois les deux côtés chargés ; ne lance jamais de
   lecture. Affiche le nombre d'éléments aux armatures différentes, un tableau par type
   (comparés, différents, part), des filtres par type et par niveau et, pour chaque
   ligne, le feuillet du plan, le fichier DA et les annotations des deux lecteurs.
   Téléchargement de `comparaison.json` et du **rapport PDF**.

La lecture complète des DA de CLP prend plusieurs dizaines de minutes sur un processeur
de portable. Les résultats sont ensuite repris du cache (`.cache/da_jobs/`), dont la clé
est le **nom et le contenu** de chaque PDF, la méthode et la version du parseur : le
même fichier garde son cache quel que soit le dossier ou la session d'où il vient.

#### Ce que fait la comparaison

- Sont **comparés** les éléments lus des deux côtés.
- L'appariement se fait d'abord sur l'étiquette identique (type, niveau, couche,
  coordonnée), puis, pour les éléments restés seuls, sur des axes voisins, de proche en
  proche : de une à quatre positions d'axe d'écart sur un axe, même ligne sur l'autre.
  À distance égale, la paire aux armatures identiques passe d'abord. Les étiquettes ne
  sont jamais réécrites : chaque ligne garde celle du plan et celle du DA, et le champ
  `coordinate_match` vaut `exact`, `couche` ou `proche`.
- Une longueur indiquée d'un seul côté n'est pas un écart.
- Dalles : seul l'acier d'intégrité est comparé, direction par direction (`NUM`,
  `ALP`) ; une direction lue d'un seul côté est ignorée.
- Seules les lignes aux **armatures différentes** sont produites (statut `changed`).

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

Fichiers et pages lus par la méthode CLP :

| Type | Fichier | Pages lues |
|---|---|---|
| Colonnes | `CLP_COLONNES Partie 3.pdf` ; de `CLP_COLONNES Partie 1.pdf`, le supplément du sous-sol (page 5) seulement | toutes |
| Radiers | `CLP_RADIERS.pdf` | toutes |
| Dalles | chaque PDF de dalle (`CLP_DALLE NIV 3.pdf`, …) | dernière (acier d'intégrité) |
| Semelles | `CLP_SEMELLES FND.pdf` | dernière |
| Poutres | `CLP_POUTRES.pdf` | dernière |

Les fichiers produits par chaque parseur et leur format sont décrits dans
[src/l2c/da/parsers/README.md](src/l2c/da/parsers/README.md).

### Notebook

`notebooks/exploration_and_colonnes_clp_demo.ipynb` explore le corpus (quels PDF ont une
couche texte, que contiennent les autres, les trois lecteurs essayés), puis déroule le
pipeline complet des colonnes de CLP (plan → DA → enregistrements communs →
comparaison → JSON et rapport PDF) en dessinant chaque étape sur le feuillet. Il appelle
les fonctions du projet (`run_plan`, `run_da`, `compare_with_totals`, les tableaux de
`app/views.py`, `build_comparison_pdf`) : il n'a pas sa propre version du pipeline.

```bash
.venv/bin/jupyter notebook notebooks/exploration_and_colonnes_clp_demo.ipynb
```

La première fois, *Run All* prend 15 à 25 minutes sur un portable : l'OCR des
colonnes du DA en prend 10 à 20. `run_da` garde chaque page lue dans `.cache/da_jobs/`,
comme pour le tableau de bord, et les fois suivantes le notebook tourne en 5 minutes
environ. Les sorties vont dans `out/notebook_colonnes_clp/`. Le notebook se commite **sans sorties** : ses figures sont des extraits de
dessins confidentiels.

### Sorties

| Fichier | Contenu |
|---|---|
| `out/<projet>/elements_plan.json` | enregistrements annexe A, côté plan |
| `out/<projet>/run_manifest.json` | version du pipeline, système d'unités détecté, état de chaque feuillet |
| `<projet>_elements_atelier.json` | enregistrements annexe A, côté DA (téléchargement du tableau de bord) |
| `<projet>_comparaison.json` | les éléments aux armatures différentes, avec les barres des deux côtés, les barres non appariées et les sources (téléchargement du tableau de bord) |
| `<projet>_rapport_comparaison.pdf` | rapport PDF de la comparaison : sommaire par type, puis chaque écart avec ses sources (téléchargement du tableau de bord) |

Les coordonnées `x`, `y` sont en points PDF, origine en haut à gauche de la page telle
qu'affichée (rotation retirée). Les longueurs et espacements sont en millimètres.

## Résultats côté plan

```
PROJET   FEUILLETS  ENREG.  radier semelle poutre mur_refend colonne dalle  UNITÉS
CLP          18      1053      77      75     27        119     395   360  impérial
WP2          33      2117      35     124     43        212     912   791  métrique
LIGREP       25      1575      30     112     33        174     677   549  métrique
EspCa3B      45      1491      54      20     31        369     644   373  métrique
```

`FEUILLETS` compte les feuillets portant de l'armature d'élément ; `make summary`
régénère ces chiffres (version `0.3.4-integrity-slabs-only` du pipeline). Une poutre est
un seul enregistrement portant toutes ses armatures ; les dalles ne comptent que l'acier
d'intégrité. Un enregistrement localisé n'est pas pour autant prouvé exact.
Les contrôles qui le sont :

- **Clé de réponse** — `l2c truth` retrouve côté plan 5 des 6 lignes de
  `CLP_dismatch.xlsx` (radier `J-10.8`, semelle `L-13`, mur `élévation B - RDC @ 2`,
  colonnes `K-6` et `I-13`). La sixième, la dalle `J-15` (`16(8)`), n'est plus retrouvée
  depuis que seuls les aciers d'intégrité des dalles sont extraits.
- **Géométrie de grille** — là où la grille par lignes et l'ancienne grille par
  étiquettes divergeaient, la première place le symbole à 0,0–0,6 pt de ses axes, la
  seconde à 20–326 pt.

Côté DA (CLP) : les poutres donnent 27 poutres / 187 armatures au plan contre
20 poutres / 162 armatures dans le DA lu.

## Tests

```bash
make test            # toute la suite
make test-coords     # pièges de coordonnées
make test-e2e        # exécutions complètes sur le corpus
.venv/bin/python -m pytest tests/test_semelle_clp.py -q     # un fichier à la fois
```

Les tests lisent le corpus réel (`L2C_CORPUS`) et sont sautés proprement s'il est
absent. La cible `make test-ui` pointe encore vers `tests/test_app.py`, qui a été
retiré avec l'ancien écran de sélection par dossier : elle échoue.

| Fichier | Ce qu'il garde |
|---|---|
| `test_coords.py` | origine de MediaBox non nulle (19 % des pages) et `/Rotate 90` (65 %) |
| `test_end_to_end.py` | volumes par projet et par type, identifiants uniques, unités, schéma, déterminisme, clé de réponse |
| `test_sheets.py` | numéro de feuillet unique par page, type cohérent avec la série |
| `test_model.py`, `test_units.py` | vocabulaire fermé des barres, conversions |
| `test_*_clp.py`, `test_da*.py` | parseurs DA, tâches d'arrière-plan et points de reprise |
| `test_comparison.py`, `test_comparison_proximity.py`, `test_*_records.py`, `test_record_formats.py` | format commun, comparaison, appariement par axes voisins |
| `test_comparison_pdf.py` | rapport PDF |
| `test_intake.py` | stockage des fichiers téléversés |

## Hypothèses

**Sur les documents**

- Le plan s'appelle `L2C_PLAN_STR_<projet>.pdf` : le tableau de bord en tire le nom du
  projet, et la ligne de commande cherche ce nom dans le dossier qu'on lui donne.
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
  `Partie 1`, reconnue à son nom de fichier ; aucune résolution générale des révisions
  n'est faite.
- Dalles : chaque appui porte un appel `NUM` et un appel `ALP`. Quand deux appels sans
  étiquette sont lus sur un appui, le premier dans l'ordre de lecture est pris pour
  `NUM`, le second pour `ALP` ; cet ordre est supposé, et marqué comme déduit.

**Sur la comparaison**

- Deux enregistrements sont appariés par type, niveau normalisé, couche de dalle et
  identifiant (coordonnée de grille ou repère). À défaut d'étiquette identique, deux
  éléments distants d'au plus quatre positions d'axe sont tenus pour le même élément.
- Une longueur absente d'un côté n'est pas une différence.
- On compare la spécification principale (quantité, diamètre, espacement). Les repères
  de façonnage, longueurs de coupe et nombres de pièces du fabricant restent dans les
  éléments de preuve et ne déclenchent pas d'écart.

## Limites connues

**Portée**

- **Une seule méthode d'analyse des DA, calée sur les formats de CLP.** Le tableau de
  bord accepte les PDF de n'importe quel projet, mais des dessins d'un autre fabricant
  (WP2, LIGREP, EspCa3B) seront lus avec les parseurs de CLP, sans garantie de résultat.
  Un lecteur de semelles pour EspCa3B existe à l'état expérimental
  (`src/l2c/da/parsers/semelle_espca3b.py`), non enregistré comme méthode.
- **Aucun lecteur DA pour les murs de refend.**
- **L'appariement par axes voisins peut associer deux éléments distincts** (jusqu'à
  quatre positions d'écart). Les lignes dont `coordinate_match` vaut `proche` sont à
  vérifier sur les sources.
- **Dalles : seul l'acier d'intégrité est traité**, des deux côtés. Les appels généraux
  de dalle du plan ne sont plus émis, et les feuillets bas et haut des DA ne sont pas
  lus. Conséquence : l'écart connu de la clé de réponse en dalle `J-15` n'est plus
  détecté.
- Dalles, semelles et poutres : seule la dernière page de chaque PDF est lue. Sept
  élévations de poutres du plan n'ont pas de vis-à-vis dans le DA lu.
- La ligne de commande ne couvre que le côté plan (`run`, `validate`) et la clé de
  réponse (`truth`) ; la lecture des DA, la comparaison et le rapport PDF passent par le
  tableau de bord.

**Exactitude**

- Seul CLP a une clé de réponse. Sur les trois autres projets, l'exactitude repose sur
  des contrôles géométriques, pas sur une vérité étiquetée.
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
- Les erreurs d'OCR résiduelles ne sont pas corrigées : elles se retrouvent dans la
  comparaison comme des armatures différentes. Le score de confiance de chaque lecture
  reste dans les sources.

**Technique**

- **Tableau de bord sous Windows.** `src/l2c/da/jobs.py` s'importe maintenant sous
  Windows (verrou `msvcrt` à la place de `fcntl`), ce qui permet au notebook d'appeler
  `run_da`. Le lancement des tâches DA en arrière-plan par le tableau de bord n'a pas été
  testé sur un Windows natif. La ligne de commande, les parseurs DA autonomes et le
  notebook y fonctionnent. En cas de problème, utiliser Linux, macOS ou WSL pour le
  tableau de bord.
- La lecture OCR est lente sur processeur seul (plusieurs dizaines de minutes pour
  CLP) ; il n'y a pas d'accélération GPU.
- Le cache des tâches DA (`.cache/da_jobs/`) contient des fichiers `pickle` : ne charger
  que ceux produits localement.
- Les PDF téléversés restent sur le disque sous `.cache/uploads/` jusqu'à `make clean`.
- `make` suppose un environnement POSIX ; sous Windows, utiliser `install.cmd` et les
  commandes directes.

## Confidentialité et licences

- Le corpus est confidentiel (consignes §4). `.gitignore` exclut les plans, les
  dossiers `DA/`, la clé de réponse, `out/`, les caches et les images. Le chemin du
  corpus est toujours un argument, jamais inscrit dans le code. `make purge` supprime
  les sorties, les caches (dont les fichiers téléversés) et toute copie du corpus sous
  le dépôt ; le corpus lui-même doit être
  supprimé du poste après l'événement.
- Aucune donnée ne quitte le poste. Les modèles OCR (poids ONNX de RapidOCR) sont des
  fichiers locaux ; s'ils sont absents, RapidOCR les télécharge une fois, à la première
  utilisation — prévoir cette étape avant de travailler hors ligne.
- Toutes les dépendances sont à code source ouvert. **PyMuPDF est sous AGPL-3.0** : sans
  conséquence pour un usage local, mais à considérer si l'outil devait être offert
  comme service réseau (voir PLAN.md §13).
