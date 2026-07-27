# Phase 0 — résultats de falsification

> État au 2026-07-25. Tous les chiffres proviennent de calculs réellement exécutés
> (`scripts/dnbr.py`), reproductibles par les commandes indiquées. Les métriques brutes
> sont dans `data/work/*/metriques.json`.

## Protocole

Le point non trivial : **un périmètre EMS n'est pas « le périmètre du feu »**, c'est
l'état du feu à une date, vu par un capteur donné. Comparer un dNBR à un périmètre EMS
sans apparier les dates n'a aucun sens — sur ces feux, la surface EMS double en quatre
jours (Landiras : 6 720 ha le 17/07, 12 552 ha le 21/07).

Le protocole retenu est donc :

1. pour chaque feu, choisir le produit EMS dont la date de situation coïncide au mieux
   avec une acquisition Sentinel-2 disponible ;
2. prendre l'image *avant* la plus proche antérieure au départ de feu (limiter l'écart
   phénologique) ;
3. clipper toute la comparaison à l'emprise analysée par EMS **∩** l'emprise de l'image
   (hors de là, EMS n'a rien cartographié : une détection n'y serait ni vraie ni fausse) ;
4. balayer les seuils dNBR plutôt que d'en fixer un a priori.

Les dates d'acquisition ne figurent que dans la légende des cartes PDF : les shapefiles
ne portent qu'un `dmg_src_id`. C'est `scripts/ems_context.py` qui les extrait.

## Appariements retenus

| Feu | Vérité EMS | Date situation | Capteur EMS | S2 avant | S2 après |
|---|---|---|---|---|---|
| Landiras (juillet) | `EMSR592_AOI01_DEL_PRODUCT` | 17/07/2022 | SPOT6/7 10:26 | 2022-07-12 | 2022-07-17 11:08 |
| La Teste-de-Buch | `EMSR592_AOI02_DEL_PRODUCT` | 17/07/2022 | SPOT7 10:26 | 2022-07-12 | 2022-07-17 11:08 |
| Landiras (août) | `EMSR619_AOI01_DEL_MONIT01` | 12/08/2022 | SPOT6 | 2022-08-06 | 2022-08-11 |
| Saumos | `EMSR633_AOI01_DEL_MONIT01` | 17/09/2022 | SPOT6/7 | 2022-09-05 | 2022-09-20 |

Pour les deux feux de juillet, l'image Sentinel-2 du 17/07 (11:08 UTC) et l'image SPOT
d'EMS (10:26 UTC) sont séparées de **42 minutes** : la comparaison isole l'écart de
méthode, pas l'écart de date.

## Résultats

IoU maximal atteint, et valeur au seuil commun 0,15 :

| Feu | Vérité (ha) | IoU max | seuil | écart surface | IoU @0,15 | précision @0,15 |
|---|---:|---:|---:|---:|---:|---:|
| Landiras (juillet) | 6 720,5 | **0,915** | 0,15 | +2,9 % | 0,915 | 0,942 |
| La Teste-de-Buch | 3 535,5 | **0,904** | 0,10 | +2,8 % | 0,894 | 0,969 |
| Landiras (août) *hors cicatrice* | 7 123,6 | **0,768** | 0,10 | −14,1 % | 0,757 | 0,976 |
| Saumos | 3 245,2 | **0,746** | 0,15 | +10,0 % | 0,746 | 0,816 |

**Le critère G0 « IoU ≳ 0,7 sur les grands feux » est atteint sur les quatre cas**, avec
un seuil unique (0,15) et sans réglage par feu.

> Ces chiffres sont ceux obtenus **après** correction du masquage de l'eau (voir « Ce que
> la vérification visuelle a corrigé »). Avant correction : La Teste-de-Buch plafonnait à
> 0,803 et Landiras (juillet) à 0,910.

## Ce que la vérification visuelle a corrigé

Les métriques seules ne suffisent pas : `scripts/apercu.py` produit, pour chaque cas, une
planche à deux panneaux (couleur naturelle après feu / dNBR) avec vérité et détections en
surimpression. Le regard a immédiatement révélé un défaut que l'IoU masquait.

### L'eau produisait des centaines d'hectares de faux positifs

Sur La Teste-de-Buch, une large tache de détection couvrait le **lac de Cazaux**. La
cause : l'eau avait été délibérément conservée au masquage, sur le raisonnement « NBR bas
aux deux dates, donc dNBR ≈ 0 ». **Ce raisonnement est faux.** En eau, B8A et B12 valent
quelques dizaines de DN ; le rapport (a−b)/(a+b) y est numériquement instable et bascule
d'une date à l'autre sur du simple bruit capteur.

Deux corrections dans `dnbr.py` :
- exclusion de la classe SCL 6 (eau) ;
- garde-fou général `REFLECTANCE_MIN = 0,05` sur la somme des réflectances, qui rattrape
  ce que le SCL manque (eaux peu profondes, ombres denses, zones humides).

Effet sur La Teste-de-Buch : précision 0,863 → **0,969**, IoU 0,803 → **0,904**, écart de
surface +6,6 % → +2,8 %. Coût : un léger recul sur Landiras (août) — Sen2Cor classe en
« eau » une partie du brûlé sous fumée dense. Le bilan reste nettement positif.

**Aucun chiffre d'IoU n'aurait signalé cette erreur** : elle se lisait comme une précision
un peu faible, pas comme un artefact. C'est l'argument pour garder une étape de contrôle
visuel dans le processus, y compris en Phase 1.

### Les faux positifs restants sont des parcelles, pas du bruit

`scripts/faux_positifs.py` sépare les polygones détectés selon qu'ils touchent ou non le
périmètre EMS, et mesure leur compacité (Polsby-Popper) :

| Feu | seuil | n hors feu | ha hors feu | % du détecté | ha médian |
|---|---:|---:|---:|---:|---:|
| Landiras (juillet) | 0,15 | 18 | 47,2 | 0,7 % | 1,50 |
| La Teste-de-Buch | 0,15 | 16 | 49,6 | 1,5 % | 2,32 |
| Landiras (août) | 0,10 | 36 | 207,4 | 3,4 % | 2,38 |
| **Saumos** | 0,15 | **60** | **614,4** | **17,2 %** | 4,02 |

Saumos est un cas à part : son rappel est bon (0,897, le feu lui-même est bien capté), mais
**17 % de la surface détectée est hors du feu**. Sur la planche, ces polygones sont
rectangulaires et alignés sur le parcellaire — signature de coupes forestières et de
travaux agricoles, pas d'incendie. C'est exactement le risque « confusion agricole » du
confusion agricole, et il explique à lui seul l'IoU le plus bas de la série.

Hypothèse examinée : Saumos a le plus long intervalle avant/après (15 jours contre 5 pour
les autres), ce qui laisse plus de temps aux travaux forestiers. **Non confirmée** : la
seule image intermédiaire (10/09) est nuageuse à 82 % sur l'AOI (17,6 % de pixels
exploitables), le test est impossible. L'intervalle long n'était donc pas un choix mais une
contrainte de couverture nuageuse — ce qui est en soi une limite opérationnelle à retenir
pour la Phase 1.

Piste de filtrage pour la Phase 1, par ordre de robustesse : exiger la proximité d'un point
chaud VIIRS (déjà prévu au plan), croiser avec un masque forestier (BD Forêt), et n'utiliser
la compacité qu'en dernier recours — elle ne discrimine pas assez ici (0,18–0,22 hors feu
contre 0,12–0,24 sur le feu).

## Ce que ces chiffres apprennent

### 1. Le seuil UN-SPIDER de 0,27 est trop haut pour la pinède landaise

Le seuil 0,27 (« sévérité faible » UN-SPIDER) sous-détecte systématiquement de 15 à 22 %
en surface. L'optimum est à **0,15**, et la meilleure fidélité de *surface* est à 0,20
(Saumos : +0,3 % d'écart à 0,20 contre +10,2 % à 0,15). Il y a donc un arbitrage explicite
à trancher : optimiser la forme (IoU) ou la surface publiée. Pour un produit dont
l'usage principal est « combien d'hectares ont brûlé », 0,20 est probablement le bon
choix — à confirmer sur d'autres milieux que la pinède.

### 2. Le dNBR est aveugle au sur-brûlage d'une cicatrice récente

Sur Landiras, 1 153 ha de la zone brûlée en août tombent dans le périmètre de juillet.
Sur cette zone, le dNBR médian est **−0,069** et seuls **0,3 %** des pixels dépassent
0,10 : la détection y est nulle, non pas dégradée. Une cicatrice fraîche est déjà sombre
en NIR/SWIR ; il n'y a plus de contraste à mesurer.

Hors de cette zone, 93,9 % des pixels de la vérité dépassent 0,10 — la méthode fonctionne
normalement. C'est donc bien une limite structurelle, pas un défaut de calibrage.

**Conséquence produit** : un feu qui rebrûle une zone brûlée dans la même saison sera
sous-estimé. À afficher sur la page « méthode et limites », et à détecter automatiquement
en Phase 1 (intersection avec les périmètres déjà publiés).

### 3. Les nuages plafonnent le rappel, et il faut le mesurer séparément

Sur Landiras (août), 12,7 % de la vérité tombe sous des pixels masqués (nuages/ombres).
Le rappel atteignable est donc **0,873**, et le rappel obtenu est 0,821 — soit 94 % du
maximum possible. Sans ce chiffre, on aurait conclu à tort à une faiblesse de la méthode.
`scripts/dnbr.py` reporte ce plafond systématiquement.

### 4. La vérité EMS est elle-même incomplète, et le dit

Les légendes portent des réserves explicites : « due to dense smoke, the burnt area
delineation is not complete », « areas that could not be analysed ». Sur les feux de
juillet, EMS cartographie sous fumée épaisse avec des trous assumés. Une part de nos
« faux positifs » (précision 0,86–0,94 au seuil optimal) est probablement du vrai brûlé
qu'EMS n'a pas pu tracer. **L'IoU mesuré est donc un plancher, pas un plafond.**

Corollaire méthodologique : EMS travaille à 0,5–1,5 m (SPOT/Pléiades), photo-interprété
au 1:10 000 avec une unité minimale de 225 m². Nous travaillons à 20 m avec une unité
minimale de 1 ha. Obtenir 0,75–0,91 d'IoU contre une référence 10 à 30 fois plus fine
est un bon résultat, pas un résultat dégradé.

## Points techniques vérifiés (à ne pas refaire)

- **Aucun compte Copernicus n'est nécessaire pour la Phase 0.** Le catalogue STAC
  d'Earth Search (`earth-search.aws.element84.com/v1`) et les COG
  (`s3://sentinel-cogs`) sont accessibles anonymement. `SETUP.md` est corrigé en ce sens.
- **Décalage radiométrique baseline 04.00 : déjà appliqué** dans cette collection. Vérifié
  empiriquement — l'eau lit B12 ≈ 43 DN (0,004 de réflectance) et non ≈ 1 043. Le champ
  STAC `raster:bands.offset = -0.1` est obsolète et contredit
  `earthsearch:boa_offset_applied = true` ; le ré-appliquer biaiserait le NBR.
- **Les classes SCL 2 (pixels sombres) et 5 (sol nu) doivent être conservées** : une zone
  fraîchement brûlée y tombe presque toujours. Les masquer reviendrait à masquer la cible.
- **La classe SCL 6 (eau) doit au contraire être exclue**, avec en plus un plancher de
  réflectance : le NBR n'a pas de sens sur les surfaces très sombres (cf. ci-dessus).
- Les paires d'images sont prises sur une même tuile MGRS, donc sur la même grille : aucun
  rééchantillonnage avant la différence. `dnbr.py` refuse explicitement les paires
  inter-tuiles.

## Reste à faire pour statuer sur G0

Le critère G0 comporte trois conditions. Une seule est tranchée.

| Condition G0 | État |
|---|---|
| IoU ≳ 0,7 sur les grands feux | **atteint** (0,748 à 0,910) |
| Feu < 30 ha détecté, surface à ±30 % | **cas sélectionné, calcul à faire** |
| Feu des Monts d'Arrée (sans vérité EMS) | **cas identifié, calcul à faire** |
| Au moins un utilisateur dit « oui » | **non fait** — entretien à mener |

### Accès BDIFF : anomalie TLS contournée proprement

`bdiff.agriculture.gouv.fr` sert une chaîne TLS incohérente : le certificat serveur est
émis par `GEANT TLS RSA 1` (Hellenic Academic and Research Institutions CA) mais le
serveur présente `GEANT OV RSA CA 4` comme intermédiaire. Aucun client ne peut construire
la chaîne — curl et Python/certifi échouent tous deux (`unable to get local issuer
certificate`). C'est une erreur de configuration côté serveur, pas un problème local.

**Résolu sans désactiver la vérification TLS.** `scripts/ca_bundle.py` récupère le
véritable intermédiaire à l'URL publiée par l'extension AIA du certificat lui-même
(`http://crt.harica.gr/HARICA-GEANT-TLS-R1.cer`), **vérifie qu'il chaîne vers une racine
déjà présente dans certifi** (`HARICA TLS RSA Root CA 2021` — `openssl verify` : OK), et
seulement alors l'ajoute à une copie locale du magasin. La validation reste complète
jusqu'à une racine de confiance ; on ne fait que fournir le maillon que le serveur omet.

Il resterait utile de signaler l'anomalie à l'exploitant.

### Cas d'étude restants, sélectionnés dans la BDIFF

`scripts/fetch_bdiff.py` rejoue le formulaire GET du portail (la BDIFF n'a pas d'API).

- **Monts d'Arrée** : Brasparts (29016), alerte **24/07/2022 23:28**, **1 917,00 ha**
  déclarés (intégralement en « Forêt »), nature inconnue. Aucune activation EMS —
  la vérité est une **surface**, pas une forme : la comparaison portera sur la
  détectabilité et l'écart de surface, pas sur un IoU.
- **Feu < 30 ha** — candidats en Gironde 2022 (même contexte landais que les cas validés) :

  | Commune | Alerte | Surface déclarée |
  |---|---|---:|
  | Queyrac | 06/08/2022 19:00 | 19,40 ha |
  | Soulac-sur-Mer | 06/08/2022 09:23 | 18,80 ha |
  | Captieux | 07/06/2022 16:12 | 13,60 ha |
  | Val-de-Livenne | 28/07/2022 15:20 | 11,70 ha |
  | Auros | 11/05/2022 17:13 | 10,50 ha |

  Queyrac et Soulac-sur-Mer sont les plus proches du seuil des 30 ha et tombent sur la
  même fenêtre d'acquisition ; Captieux est en pinède landaise, donc le plus comparable
  aux cas déjà validés.

**Limite à garder en tête** : la BDIFF est déclarative et communale, sans géométrie. Elle
ne permet pas de calculer un IoU, seulement un écart de surface — et sa propre surface
déclarée porte une incertitude non documentée. Un écart de 20 % peut venir de la BDIFF
autant que de nous.

## Reproduire

```bash
uv run python scripts/fetch_ems.py        # paquets vecteurs + cartes PDF
uv run python scripts/ems_context.py      # dates d'acquisition depuis les légendes
uv run python scripts/build_reference.py  # couche de vérité + emprises

uv run python scripts/dnbr.py --produit EMSR592_AOI01_DEL_PRODUCT_r1_RTP01_v1 \
    --pre S2A_30TXQ_20220712_0_L2A --post S2B_30TXQ_20220717_0_L2A
uv run python scripts/dnbr.py --produit EMSR592_AOI02_DEL_PRODUCT_r1_RTP01_v1 \
    --pre S2A_30TXQ_20220712_0_L2A --post S2B_30TXQ_20220717_0_L2A
uv run python scripts/dnbr.py --produit EMSR633_AOI01_DEL_MONIT01_r1_VECTORS_v1 \
    --pre S2B_30TXQ_20220905_0_L2A --post S2A_30TXQ_20220920_0_L2A
uv run python scripts/dnbr.py --produit EMSR619_AOI01_DEL_MONIT01_r1_RTP01_v1 \
    --pre S2B_30TXQ_20220806_0_L2A --post S2A_30TXQ_20220811_0_L2A \
    --exclure-produit EMSR592_AOI01_GRA_PRODUCT_r1_RTP01_v1 --seuil-retenu 0.10

uv run python scripts/apercu.py         # planches de vérification (PNG par cas)
uv run python scripts/faux_positifs.py  # détections hors périmètre de référence
uv run python scripts/projet_qgis.py    # projet QGIS pour l'inspection fine
uv run python scripts/visionneuse.py    # visionneuse HTML autonome (zoom, calques)
```

## Vérifier visuellement

- **Planches PNG** : `data/work/<cas>/apercu.png`. Deux panneaux — couleur naturelle après
  feu (10 m) et dNBR — avec périmètre EMS en rouge et détections en bleu, contours seuls.
  C'est sur le panneau couleur naturelle qu'on reconnaît une parcelle agricole, une coupe
  forestière ou une ombre de nuage.
- **Visionneuse HTML** : `data/work/visionneuse.html` (`scripts/visionneuse.py`). Un seul
  fichier de ~9 Mo, ouvert par double-clic, **sans build ni dépendance JS** — le rendu est
  un canvas d'une centaine de lignes. Zoom, déplacement, bascule des calques
  (Sentinel-2 / dNBR / vérité EMS / détections), clic sur un polygone pour sa surface et
  son statut (sur le feu / hors périmètre, ces derniers en jaune).

  Tout est embarqué en data URI : en `file://`, un `fetch()` d'un GeoJSON voisin serait
  bloqué. La géométrie est projetée côté Python dans le repère pixel de chaque image, donc
  le JavaScript ne fait aucun calcul de projection.

  C'est un **outil de contrôle Phase 0, jetable par construction** — pas une préfiguration
  du site public, qui sera statique (MapLibre + PMTiles) et ne viendra qu'après G0 et G1.

- **Projet QGIS** : `data/work/brulis.qgs`, 10 couches pré-stylées, chemins relatifs,
  rasters dNBR décochés par défaut. Pour l'inspection fine (mesures, fonds tiers).
  ⚠️ Généré mais **non ouvert pour vérification** : QGIS n'est pas installé sur la machine
  de développement. À défaut, les `.geojson` et `.tif` se chargent par glisser-déposer.
