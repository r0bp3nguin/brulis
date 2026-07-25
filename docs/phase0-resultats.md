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

| Feu | Vérité (ha) | IoU max | seuil | écart surface | IoU @0,15 |
|---|---:|---:|---:|---:|---:|
| Landiras (juillet) | 6 720,5 | **0,910** | 0,15 | +3,5 % | 0,910 |
| La Teste-de-Buch | 3 535,5 | **0,803** | 0,15 | +6,6 % | 0,803 |
| Landiras (août) *hors cicatrice* | 7 123,6 | **0,781** | 0,10 | −12,7 % | 0,771 |
| Saumos | 3 245,2 | **0,748** | 0,15 | +10,2 % | 0,748 |
| Landiras (août) *brut* | 8 276,9 | 0,677 | 0,10 | −24,9 % | 0,665 |

**Le critère G0 « IoU ≳ 0,7 sur les grands feux » est atteint sur les quatre cas**, avec
un seuil unique (0,15) et sans réglage par feu.

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
- Les paires d'images sont prises sur une même tuile MGRS, donc sur la même grille : aucun
  rééchantillonnage avant la différence. `dnbr.py` refuse explicitement les paires
  inter-tuiles.

## Reste à faire pour statuer sur G0

Le critère G0 comporte trois conditions. Une seule est tranchée.

| Condition G0 | État |
|---|---|
| IoU ≳ 0,7 sur les grands feux | **atteint** (0,748 à 0,910) |
| Feu < 30 ha détecté, surface à ±30 % | **non testé** — bloqué sur l'accès BDIFF |
| Feu des Monts d'Arrée (sans vérité EMS) | **non testé** — bloqué sur l'accès BDIFF |
| Au moins un utilisateur dit « oui » | **non fait** — entretien à mener |

### Blocage BDIFF

`bdiff.agriculture.gouv.fr` sert une chaîne TLS incohérente : le certificat serveur est
émis par `GEANT TLS RSA 1` (Hellenic Academic and Research Institutions CA) mais le
serveur présente `GEANT OV RSA CA 4` comme intermédiaire. Aucun client ne peut construire
la chaîne — curl et Python/certifi échouent tous deux (`unable to get local issuer
certificate`). C'est une erreur de configuration côté serveur, pas un problème local.

La vérification TLS n'a **pas** été désactivée. Pistes, par ordre de préférence :
1. récupérer le véritable intermédiaire `GEANT TLS RSA 1` depuis l'extension AIA du
   certificat et l'ajouter à un magasin local du projet — la validation reste complète
   jusqu'à une racine de confiance ;
2. chercher un miroir de la BDIFF sur data.gouv.fr (l'entrée actuelle ne pointe que vers
   le portail) ;
3. signaler l'anomalie à l'exploitant.

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
    --exclure-produit EMSR592_AOI01_GRA_PRODUCT_r1_RTP01_v1
```
