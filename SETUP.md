# Setup — Brûlis

## Comptes et accès (tous gratuits)

1. **Sentinel-2 L2A — aucun compte nécessaire.** Vérifié en Phase 0 : le catalogue STAC
   d'Earth Search (`https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`)
   et les COG associés (`s3://sentinel-cogs`, via `AWS_NO_SIGN_REQUEST=YES`) sont accessibles
   anonymement, bandes B8A/B12/SCL comprises. C'est la source utilisée par `scripts/dnbr.py`.
   Un compte **Copernicus Data Space Ecosystem** (dataspace.copernicus.eu) reste utile si l'on
   veut openEO/Sentinel Hub ou des produits absents d'AWS — pas requis avant d'en avoir besoin.
2. **NASA FIRMS** (firms.modaps.eosdis.nasa.gov) : demander une « map key » gratuite pour l'API
   (points chauds VIIRS/MODIS, amorces de détection). Nécessaire à partir de la Phase 1.
3. GitHub : dépôt public dès le départ (le code ouvert fait partie du produit).

## Environnement local

```bash
# Python ≥ 3.11, gestion par uv (ou venv)
uv sync                     # dépendances figées dans pyproject.toml / uv.lock
uv run python scripts/fetch_ems.py
# Vérification visuelle : QGIS (brew install --cask qgis)
```

**Piège PROJ/GDAL.** Si le shell exporte `PROJ_LIB`, `PROJ_DATA` ou `GDAL_DATA` vers une autre
installation (Anaconda, Homebrew), rasterio échoue sur la résolution des codes EPSG
(`CRSError: The EPSG code is unknown … comes from another PROJ installation`). Les scripts
importent `scripts/env_geo.py` en premier, qui neutralise ces variables si elles pointent hors
du venv — rien à faire côté shell.

Disque : les scripts lisent les COG Sentinel-2 par fenêtre, à distance ; aucune tuile complète
n'est téléchargée. Prévoir ~250 Mo pour les paquets EMS et les sorties de travail.

## Données de référence (Phase 0)

- **Périmètres officiels 2022** : activations Copernicus EMS Gironde-Landes EMSR592, EMSR619, EMSR633 —
  produits vectoriels téléchargeables sur mapping.emergency.copernicus.eu (vérité terrain principale).
- **BDIFF** (bdiff.agriculture.gouv.fr + data.gouv.fr) : surfaces déclarées 2022, pour le feu des
  Monts d'Arrée et le petit feu témoin (< 30 ha) à choisir dedans.
  ⚠️ **Accès bloqué au 2026-07-25** : le serveur présente un intermédiaire TLS qui n'est pas
  l'émetteur de son certificat (leaf émis par `GEANT TLS RSA 1`, intermédiaire servi
  `GEANT OV RSA CA 4`) ; aucune chaîne valide ne peut être construite. Ne pas désactiver la
  vérification TLS — voir `docs/phase0-resultats.md` pour le diagnostic et les pistes.
- **Masque végétation** : BD Forêt (IGN, ouverte) ou Corine Land Cover / OSM landuse.

## Méthode (référence)

Détection par différence d'indice de brûlure normalisé (dNBR = NBR_avant − NBR_après, bandes B8A/B12 de
Sentinel-2), seuils type UN-SPIDER, filtrage par masque végétation et amorces VIIRS, puis polygonisation.
Ne pas réinventer : partir de cette pratique documentée et l'ajuster aux cas français.

Ajustements mesurés en Phase 0 (détail et chiffres : `docs/phase0-resultats.md`) :

- **seuil 0,15–0,20 et non 0,27** sur pinède landaise ; 0,27 sous-détecte de 15 à 22 % en surface ;
- **ne pas ré-appliquer le décalage baseline 04.00** : il est déjà appliqué dans la collection
  `sentinel-2-l2a` d'Earth Search (vérifié sur l'eau : B12 ≈ 43 DN). Le champ STAC
  `raster:bands.offset = -0.1` est obsolète ;
- **conserver les classes SCL 2 et 5** au masquage : une zone fraîchement brûlée y tombe presque
  toujours ;
- **le sur-brûlage d'une cicatrice récente est indétectable** (dNBR médian −0,07) : le signaler
  plutôt que tenter de le corriger.

## Hébergement (à partir de la Phase 2)

Publication statique d'abord : GeoJSON/PMTiles sur stockage objet (Scaleway/OVH ~1-5 €/mois) + carte
MapLibre GL JS + domaine (~10 €/an). API dynamique (FastAPI) seulement si le besoin est prouvé.
Calcul : en local au début ; petite VM saisonnière (10-40 €/mois) si nécessaire en Phase 3.

## Garde-fous

- Toujours afficher date de donnée, latence et incertitude sur chaque périmètre publié.
- Ne jamais présenter le produit comme un outil opérationnel ou de détection.
- Licence : code MIT/Apache-2.0, données produites en ODbL ou Licence Ouverte 2.0.
