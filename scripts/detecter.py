"""Détection automatique : points chauds VIIRS → périmètre Sentinel-2, sans intervention.

Chaîne complète, c'est le cœur du produit :

    FIRMS (VIIRS, ~3 h)  →  regroupement en foyers  →  recherche STAC Sentinel-2
                         →  dNBR  →  polygone  →  data/feux/<id>/

Choix des images, qui fait toute la qualité du résultat :
  - **après** : l'acquisition la plus récente postérieure au premier point chaud, dont la
    part de pixels exploitables sur la zone dépasse `COUVERTURE_MIN`. On ne se fie pas au
    `eo:cloud_cover` de la tuile entière : un nuage à 60 km de la zone ne gêne en rien.
  - **avant** : la plus récente acquisition claire *antérieure* au feu, dans une fenêtre de
    `JOURS_AVANT`. Plus elle est proche, moins l'écart de végétation pollue le dNBR.
  - les deux doivent être sur la **même tuile MGRS**, donc la même grille : aucun
    rééchantillonnage avant la différence.

Un feu encore actif donne un périmètre partiel : c'est normal, il est daté et sera
recalculé au passage suivant, dans la même fiche — un incendie garde un identifiant du
premier au dernier jour (voir `JOURS_MEME_FEU`).

Usage :
    python scripts/detecter.py --jours 5 --max-foyers 10
    python scripts/detecter.py --foyer data/work/foyers.geojson --max-foyers 3
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from pystac_client import Client
from shapely.geometry import box

import firms
from dnbr import (COLLECTION, CRS_METRIQUE, SCL_INVALIDE, SEUILS, STAC, lire, nbr,
                  polygoniser)

SEUIL_DEFAUT = 0.15        # optimum mesuré sur pinède landaise (docs/phase0-resultats.md)
SURFACE_MIN_HA = 1.0
MARGE_M = 3000             # marge autour du foyer pour capter le feu au-delà des points chauds
JOURS_AVANT = 60           # fenêtre de recherche de l'image avant-feu
COUVERTURE_MIN = 0.55      # part minimale de pixels exploitables sur la zone
DISTANCE_RATTACHEMENT = 3000  # un polygone au-delà n'est pas rattaché à ce foyer

# Demi-côté d'un pixel VIIRS (375 m au nadir). Tamponner les points chauds de cette
# distance approche l'emprise vue comme chaude au moment des passages.
#
# Ce n'est PAS un périmètre brûlé, et ce n'est PAS une borne. Confronté aux deux seuls
# cas où une surface de référence existait le jour même (25/07/2026) :
#   Biscarrosse   4 356 ha estimés contre  3 500 ha rapportés  -> +24 %
#   Gironde      20 282 ha estimés contre 30 000 ha rapportés  -> -32 %
# La sous-estimation vient du principe même de la mesure : VIIRS ne voit que les fronts
# actifs à l'instant du passage ; ce qui a brûlé puis refroidi entre deux passages
# n'apparaît pas. Sur un feu très rapide, la perte domine.
#
# On publie donc un ordre de grandeur explicitement incertain, jamais un chiffre net.
DEMI_PIXEL_VIIRS = 187
INCERTITUDE_ESTIMEE = 0.35  # ±35 %, enveloppe des deux écarts observés

# Couvert végétal avant feu, mesuré par le NBR de l'image « avant » sur la zone détectée.
# Une forêt ou une lande dense se situe vers 0,3–0,5 ; un champ moissonné, un sol nu ou une
# zone industrielle vers 0,0–0,15. C'est ce qui sépare un feu de végétation d'un brûlage de
# chaume ou d'une torchère — nombreux en plaine fin juillet, en pleine moisson.
#
# Ce test ne coûte aucune lecture supplémentaire : le NBR avant est déjà calculé pour le
# dNBR. Il remplace le filtre par occupation du sol (Corine Land Cover), dont le service
# interrogeable de la Géoplateforme renvoie « LayerNotDefined » au 25/07/2026.
NBR_VEGETATION_MIN = 0.20

# Rattachement d'un foyer à la fiche qu'il continue.
#
# La fenêtre FIRMS glisse d'un jour par jour : le plus ancien point chaud encore visible
# recule, donc la date de départ *observée* d'un foyer avance, alors que l'incendie, lui,
# n'a pas bougé. Bâtir l'identifiant sur cette date ouvrait un dossier neuf chaque jour
# pour un même feu, et l'ancien n'était plus jamais rouvert : figé avec son « prochain
# passage », périmé dès le lendemain. Chouppes (86) en avait trois, du 23 au 25/07/2026 ;
# le premier annonçait encore le 30/07 une image « attendue le 26 », alors que le feu
# était mesuré depuis le 27. 25 fiches sur 40 en attente étaient dans ce cas.
#
# L'identifiant se rattache donc à la fiche que le foyer continue, et sa date reste celle
# de la première détection. Au-delà de ce délai sans le moindre point chaud, un feu au même
# endroit redevient un feu nouveau — sans quoi deux saisons finiraient par se confondre.
JOURS_MEME_FEU = 10

# Le rattachement est géométrique et non nominal : le nom de commune et le repérage d'une
# fiche sortent du centroïde du foyer, qui se déplace à mesure que le feu grandit. Un même
# incendie landais est ainsi passé de « Le Temple » à « Le Porge » en trois jours, et
# Biscarrosse a changé de repérage d'un centième de degré. Se fier au nom rouvrirait la
# même plaie : une fiche neuve chaque fois que le feu se déporte.
#
# Le critère est le recouvrement, pas la proximité. Deux incendies voisins finissent par
# se toucher sans être le même feu — dans le Var, les cicatrices de Pontevès, Correns et
# Barjols sont jointives ; dans les Landes, celles du Porge et de Lège-Cap-Ferret. Un
# simple contact les aurait toutes fondues en une. Un feu qui grandit, lui, recouvre
# largement ce qu'il occupait la veille.
#
# Même seuil et même raison que `site.RECOUVREMENT_DOUBLON`, qui tranche la même question
# au moment de publier.
RECOUVREMENT_MEME_FEU = 0.3

# Reprise des feux restés sans périmètre.
#
# Un feu en attente d'image quitte la fenêtre FIRMS dès que ses points chauds ont quelques
# jours. Plus aucun foyer ne le représente, donc la détection ne le revoit jamais : il
# reste « en attente » indéfiniment, alors que l'image qui lui manquait finit presque
# toujours par arriver. Au 30/07/2026, quinze fiches étaient dans ce cas, dont plusieurs
# affirmaient encore « aucune image satellite depuis le départ du feu » une semaine après.
#
# On les réexamine donc à partir de ce que l'archive contient déjà. Au-delà de ce délai on
# cesse : la cicatrice s'estompe, et un feu qui n'a rien donné en trois semaines n'a plus
# de raison d'en donner.
JOURS_REPRISE = 21


def nommer(lat: float, lon: float) -> dict:
    """Commune la plus proche (geo.api.gouv.fr, libre et sans clé)."""
    url = (f"https://geo.api.gouv.fr/communes?lat={lat:.5f}&lon={lon:.5f}"
           "&fields=nom,codeDepartement,codesPostaux&format=json")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        if data:
            return {"commune": data[0].get("nom", ""),
                    "departement": data[0].get("codeDepartement", "")}
    except Exception:
        pass
    return {"commune": "", "departement": ""}


def tuile_de(it) -> str:
    return it.properties.get("grid:code") or it.id.split("_")[1]


def epsg_de(it) -> int:
    if "proj:epsg" in it.properties:
        return int(it.properties["proj:epsg"])
    return int(str(it.properties["proj:code"]).split(":")[-1])


def _emprise(it, zone_m, marge=MARGE_M):
    crs = rasterio.crs.CRS.from_epsg(epsg_de(it))
    bounds = gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).buffer(marge).total_bounds
    return crs, bounds


def couverture(it, zone_m, marge=MARGE_M) -> float:
    """Part de pixels exploitables sur la zone, d'après la seule classification de scène.

    Le tri des images candidates ne demande pas les valeurs spectrales : SCL suffit à
    savoir si la zone est dégagée. Ne lire qu'une bande au lieu de trois divise par trois
    le trafic réseau de l'étape la plus coûteuse — jusqu'à 14 candidats sont examinés par
    foyer, contre 2 finalement retenus.
    """
    crs, bounds = _emprise(it, zone_m, marge)
    scl, transform, _ = lire(it, bounds, "scl")
    if scl.size == 0:
        return 0.0
    exploitable = ~np.isin(scl, list(SCL_INVALIDE))
    zone_locale = gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).iloc[0]
    dans = rasterio.features.geometry_mask(
        [zone_locale], out_shape=scl.shape, transform=transform, invert=True)
    if dans.sum() == 0:
        return 0.0
    return float((exploitable & dans).sum() / dans.sum())


def lire_nbr(it, zone_m, marge=MARGE_M):
    """Lecture complète (B8A, B12, SCL) : réservée aux deux images retenues."""
    crs, bounds = _emprise(it, zone_m, marge)
    valeurs, valide, transform, _ = nbr(it, bounds)
    return valeurs, valide, transform, crs


_CACHE_PASSAGES: dict[tuple, str | None] = {}


def prochain_passage(client: Client, bbox) -> str | None:
    """Date du prochain passage Sentinel-2 attendu, d'après le rythme observé.

    Sans cette information, un feu sans image ne dit rien de ce qu'il faut attendre.
    Le rythme dépend du lieu (recouvrement des orbites) : on le mesure au lieu de
    supposer les 5 jours théoriques.
    """
    cle = tuple(round(v, 1) for v in bbox)  # ~10 km : les foyers voisins partagent le rythme
    if cle in _CACHE_PASSAGES:
        return _CACHE_PASSAGES[cle]
    passes = sorted({i.datetime.date() for i in client.search(
        collections=[COLLECTION], bbox=list(bbox),
        datetime="2026-06-01/2026-12-31").item_collection()})
    if len(passes) < 3:
        _CACHE_PASSAGES[cle] = None
        return None
    # Intervalle le plus long récemment observé, pas le plus court : les passages
    # rapprochés viennent d'orbites adjacentes qui n'effleurent parfois que le bord de la
    # zone. Retenir le minimum annonçait une image « pour le 25 » qui n'est jamais venue —
    # une promesse ratée coûte plus cher qu'une estimation prudente.
    ecarts = [e for e in ((passes[k + 1] - passes[k]).days
                          for k in range(len(passes) - 1)) if e > 0]
    typique = max(ecarts[-6:]) if ecarts else 5
    prevu = passes[-1] + timedelta(days=typique)
    aujourd = datetime.now(timezone.utc).date()
    while prevu < aujourd:
        prevu += timedelta(days=typique)
    _CACHE_PASSAGES[cle] = prevu.isoformat()
    return _CACHE_PASSAGES[cle]


# Tolérances de simplification, choisies sous la résolution de la donnée source : on ne
# jette aucune information réelle, seulement des sommets que rien ne justifie. Sans cela,
# l'union de milliers de disques VIIRS produit des géométries de dizaines de milliers de
# sommets, recommitées deux fois par jour.
SIMPLIFICATION_EMPRISE = 60   # m, contre 375 m de pixel VIIRS
SIMPLIFICATION_PERIMETRE = 8  # m, contre 20 m de pixel Sentinel-2


def emprise_points_chauds(foyer, points: gpd.GeoDataFrame | None):
    """Emprise chaude observée : ordre de grandeur en attendant une image exploitable."""
    if points is not None and len(points):
        # Résolution basse : un disque de 187 m n'a pas besoin de 64 segments quand la
        # donnée sous-jacente est un pixel de 375 m. Le paramètre de GeoSeries.buffer
        # s'appelle `resolution` — `quad_segs` est celui de shapely et lève ici.
        return (points.to_crs(CRS_METRIQUE).buffer(DEMI_PIXEL_VIIRS, resolution=4)
                .union_all().simplify(SIMPLIFICATION_EMPRISE))
    return foyer.geometry


_ARCHIVE: list[dict] | None = None


def archive(sortie: Path) -> list[dict]:
    """Index des fiches déjà écrites : dossier, bornes de dates, géométrie.

    Lu une fois par exécution. Les fiches créées pendant l'exécution n'y entrent pas, et
    n'ont pas à y entrer : deux foyers d'un même passage sont séparés d'au moins 2 km,
    donc aucun ne peut prétendre continuer la fiche que l'autre vient d'ouvrir.
    """
    global _ARCHIVE
    if _ARCHIVE is not None:
        return _ARCHIVE
    _ARCHIVE = []
    if not sortie.exists():
        return _ARCHIVE
    for dossier in sorted(sortie.iterdir()):
        if not dossier.is_dir():
            continue
        geo = next((dossier / n for n in ("perimetre.geojson", "emprise.geojson")
                    if (dossier / n).exists()), None)
        if geo is None:
            continue
        try:
            info = json.loads((dossier / "info.json").read_text(encoding="utf-8"))
            premier = str(info["premier_point_chaud"])[:10]
            dernier = str(info.get("dernier_point_chaud") or premier)[:10]
            date.fromisoformat(dernier)
            geometrie = gpd.read_file(geo).to_crs(CRS_METRIQUE).union_all()
        except (OSError, ValueError, KeyError, TypeError):
            continue  # fiche illisible : elle ne servira pas de point d'ancrage
        _ARCHIVE.append({"dossier": dossier.name, "premier": premier,
                         "dernier": dernier, "geometry": geometrie})
    return _ARCHIVE


def feu_deja_suivi(sortie: Path, zone_m, debut: datetime) -> dict | None:
    """Fiche que ce foyer continue, s'il continue bien quelque chose.

    Deux conditions : la géométrie archivée recouvre le foyer courant d'au moins
    `RECOUVREMENT_MEME_FEU`, et les périodes d'activité se suivent — une fiche dont le
    dernier point chaud remonte à plus de `JOURS_MEME_FEU` décrit un autre incendie.
    """
    candidats = []
    for fiche in archive(sortie):
        try:
            ecart = (debut.date() - date.fromisoformat(fiche["dernier"])).days
        except ValueError:
            continue
        if ecart > JOURS_MEME_FEU:
            continue
        commune = fiche["geometry"].intersection(zone_m).area
        reference = min(fiche["geometry"].area, zone_m.area)
        if reference > 0 and commune > RECOUVREMENT_MEME_FEU * reference:
            candidats.append((-commune / reference, fiche["premier"], fiche))
    if not candidats:
        return None
    # Le recouvrement le plus franc ; à égalité, la fiche la plus ancienne, qui porte la
    # vraie date de départ du feu.
    return min(candidats, key=lambda c: (c[0], c[1]))[2]


def foyers_en_attente(sortie: Path, deja: set[str],
                      jours: int = JOURS_REPRISE) -> gpd.GeoDataFrame:
    """Feux encore sans périmètre, à réexaminer bien qu'ils aient quitté la fenêtre FIRMS.

    Reconstitue un foyer avec ce que l'archive contient déjà : l'emprise des points chauds
    tient lieu de géométrie, les dates et les décomptes sont relus de l'`info.json`. Le
    résultat a les mêmes colonnes qu'un foyer FIRMS, et passe donc par `traiter` sans rien
    y changer.
    """
    vide = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=CRS_METRIQUE)
    if not sortie.exists():
        return vide
    aujourd = datetime.now(timezone.utc).date()
    lignes = []
    for dossier in sorted(sortie.iterdir()):
        emprise = dossier / "emprise.geojson"
        if not dossier.is_dir() or dossier.name in deja or not emprise.exists():
            continue
        try:
            info = json.loads((dossier / "info.json").read_text(encoding="utf-8"))
            debut = str(info["premier_point_chaud"])[:10]
            fin = str(info.get("dernier_point_chaud") or debut)[:10]
            age = (aujourd - date.fromisoformat(fin)).days
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if info.get("statut") != "en_attente" or age > jours:
            continue
        lignes.append({
            "foyer": dossier.name,
            "n_points": int(info.get("n_points_chauds") or 0),
            "date_debut": debut,
            "date_fin": fin,
            "frp_max": None,
            "frp_total": info.get("frp_total"),
            "geometry": gpd.read_file(emprise).to_crs(CRS_METRIQUE).union_all(),
        })
    if not lignes:
        return vide
    return (gpd.GeoDataFrame(lignes, geometry="geometry", crs=CRS_METRIQUE)
            .sort_values("frp_total", ascending=False).reset_index(drop=True))


def traiter(foyer, client: Client, seuil: float, sortie: Path,
            points: gpd.GeoDataFrame | None = None) -> dict | None:
    zone_m = foyer.geometry
    # Tampon en mètres puis reprojection : bufferiser en degrés déforme selon la latitude.
    bbox = (gpd.GeoSeries([zone_m], crs=CRS_METRIQUE)
            .buffer(MARGE_M * 2).to_crs(4326).total_bounds)
    # Relu depuis un GeoJSON, `date_debut` peut revenir en date/Timestamp et non en texte.
    brut = foyer["date_debut"]
    debut = (brut if isinstance(brut, datetime)
             else datetime.fromisoformat(str(brut)[:10])).replace(tzinfo=timezone.utc)

    centre = gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(4326).iloc[0].centroid
    lieu = nommer(centre.y, centre.x)
    # L'emprise FIRMS est un rectangle : il déborde sur l'Espagne, l'Italie, l'Allemagne,
    # la Belgique, la Suisse et le Luxembourg. Sans ce test, des feux étrangers sont
    # publiés comme français — 28 cas sur 68 lors de la campagne du 25/07/2026.
    # L'absence de commune dans le référentiel officiel vaut « hors de France ».
    if not lieu["commune"]:
        print(f"  foyer {foyer['foyer']} ({centre.y:.2f},{centre.x:.2f}) — hors de France, ignoré")
        return None
    # Suffixe géographique : sans lui, deux foyers distincts d'une même commune au même
    # jour produisent le même identifiant et s'écrasent en silence sur le disque — 23
    # fiches sur 68 perdues lors de la campagne du 25/07/2026. Arrondi à ~1 km pour
    # rester stable d'une exécution à l'autre.
    # Repérage au centième de degré (~1 km) : distingue deux foyers d'une même commune
    # sans dépendre d'une précision que le centroïde n'a pas quand le feu grandit.
    reperage = f"{abs(centre.y):.2f}{abs(centre.x):.2f}".replace(".", "")
    etiquette = f"{lieu['commune']} ({lieu['departement']})"
    print(f"\n  {etiquette} — {foyer['n_points']} points chauds, "
          f"{foyer['date_debut']} → {foyer['date_fin']}")

    suivi = feu_deja_suivi(sortie, zone_m, debut)
    dernier = str(foyer["date_fin"])[:10]
    if suivi:
        identifiant = suivi["dossier"]
        dernier = max(dernier, suivi["dernier"])
        # Le feu a commencé au premier point chaud jamais vu, pas au plus ancien encore
        # dans la fenêtre FIRMS. C'est cette date qui borne la recherche d'images : sans
        # elle, l'image « avant » pouvait être prise en plein incendie, et la latence
        # annoncée était comptée depuis le mauvais jour.
        debut = min(debut, datetime.fromisoformat(suivi["premier"])
                    .replace(tzinfo=timezone.utc))
        print(f"     suite de la fiche {identifiant} (départ {debut:%Y-%m-%d})")
    else:
        identifiant = (f"{debut:%Y%m%d}-{lieu['departement'] or '00'}-"
                       f"{lieu['commune'].lower().replace(' ', '-')[:24]}-{reperage}")

    def en_attente(motif: str) -> dict:
        """Publier ce que l'on sait déjà plutôt que rien : un grand feu sans image
        satellite doit apparaître, avec une estimation clairement majorante."""
        geom = emprise_points_chauds(foyer, points)
        estimee = geom.area / 1e4
        attendu = prochain_passage(client, bbox)
        dossier = sortie / identifiant
        # Une mesure ne doit jamais être remplacée par une simple estimation : si un
        # périmètre existe déjà pour ce feu, on le conserve.
        if (dossier / "perimetre.geojson").exists():
            print("     déjà mesuré précédemment — estimation non écrite")
            return None
        dossier.mkdir(parents=True, exist_ok=True)
        info = {
            "id": identifiant, "feu": etiquette, "statut": "en_attente",
            "motif_attente": motif,
            "commune": lieu["commune"], "departement": lieu["departement"],
            "surface_estimee_ha": round(estimee, 1),
            "surface_min_ha": round(estimee * (1 - INCERTITUDE_ESTIMEE), 0),
            "surface_max_ha": round(estimee * (1 + INCERTITUDE_ESTIMEE), 0),
            "premier_point_chaud": f"{debut:%Y-%m-%d}",
            "dernier_point_chaud": dernier,
            "n_points_chauds": int(foyer["n_points"]),
            "frp_total": float(foyer["frp_total"]) if foyer.get("frp_total") is not None else None,
            "prochain_passage": attendu,
            "calcule_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (dossier / "info.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        gpd.GeoDataFrame({"geometry": [geom]}, geometry="geometry", crs=CRS_METRIQUE) \
            .to_crs(4326).to_file(dossier / "emprise.geojson", driver="GeoJSON")
        print(f"     → EN ATTENTE ({motif}) : {estimee * (1 - INCERTITUDE_ESTIMEE):.0f}"
              f"–{estimee * (1 + INCERTITUDE_ESTIMEE):.0f} ha d'emprise chaude, "
              f"prochain passage {attendu or 'inconnu'}")
        return info

    def ecarter(motif: str) -> None:
        """Retirer de la publication une fiche que l'examen a disqualifiée.

        Une fiche ouverte en attente d'image finit par en obtenir une. Quand celle-ci
        montre qu'il n'y a pas eu de feu de végétation, la laisser en attente promettrait
        indéfiniment une mesure qui ne viendra jamais : Vitrolles (13) annonçait un passage
        pour le 28/07 alors que l'image du 27 était nette et sans cicatrice.

        La fiche reste dans l'archive, avec le motif et la date. C'est une décision de
        retrait : elle doit rester vérifiable.
        """
        fiche = sortie / identifiant / "info.json"
        if not fiche.exists():
            return  # rien n'a jamais été publié pour ce foyer, il n'y a rien à retirer
        try:
            info = json.loads(fiche.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        # Un périmètre déjà mesuré ne se retire pas sur un examen ultérieur : c'est la même
        # règle qu'en attente, une mesure prime sur tout ce qui vient après.
        if info.get("statut") == "mesure":
            return
        info |= {"statut": "ecarte", "motif_ecarte": motif,
                 "calcule_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        info.pop("prochain_passage", None)
        fiche.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"     → ÉCARTÉ : {motif}")

    apres_max = datetime.now(timezone.utc) + timedelta(days=1)
    candidats = sorted(
        client.search(collections=[COLLECTION], bbox=list(bbox),
                      datetime=f"{debut:%Y-%m-%d}/{apres_max:%Y-%m-%d}").item_collection(),
        key=lambda i: i.datetime, reverse=True)
    if not candidats:
        return en_attente("aucune image satellite depuis le départ du feu")

    # On teste les plus récentes d'abord et on s'arrête à la première exploitable.
    for it_post in candidats[:6]:
        part = couverture(it_post, zone_m)
        marque = "retenue" if part >= COUVERTURE_MIN else "écartée"
        print(f"     après  {it_post.datetime:%Y-%m-%d} {tuile_de(it_post)} "
              f"exploitable {part:.0%} — {marque}")
        if part >= COUVERTURE_MIN:
            break
    else:
        return en_attente("images trop couvertes par les nuages ou la fumée")

    nbr_post, val_post, transform, crs = lire_nbr(it_post, zone_m)
    tuile = tuile_de(it_post)

    # Image avant : même tuile obligatoire, la plus récente qui soit claire sur la zone.
    avant = [i for i in sorted(
        client.search(collections=[COLLECTION], bbox=list(bbox),
                      datetime=f"{debut - timedelta(days=JOURS_AVANT):%Y-%m-%d}/"
                               f"{debut:%Y-%m-%d}").item_collection(),
        key=lambda i: i.datetime, reverse=True) if tuile_de(i) == tuile]

    for it_pre in avant[:8]:
        part = couverture(it_pre, zone_m)
        marque = "retenue" if part >= COUVERTURE_MIN else "écartée"
        print(f"     avant  {it_pre.datetime:%Y-%m-%d} exploitable {part:.0%} — {marque}")
        if part >= COUVERTURE_MIN:
            break
    else:
        return en_attente("aucune image avant-feu exploitable pour la comparaison")

    nbr_pre, val_pre, transform_pre, _ = lire_nbr(it_pre, zone_m)
    if nbr_pre.shape != nbr_post.shape:
        print("     fenêtres de lecture incohérentes — foyer ignoré")
        return None

    exploitable = val_pre & val_post
    dnbr = nbr_pre - nbr_post

    polys = polygoniser(exploitable & (dnbr >= seuil), transform, crs, SURFACE_MIN_HA)
    if polys.empty:
        print("     aucun polygone au-dessus du seuil")
        ecarter("images exploitables, mais aucune trace de brûlé au seuil retenu")
        return None

    # Rattachement au foyer : un brûlis à 20 km n'est pas cet incendie.
    proches = polys[polys.distance(zone_m) <= DISTANCE_RATTACHEMENT]
    if proches.empty:
        print("     polygones trouvés mais aucun près des points chauds")
        ecarter("traces de brûlé sur l'image, mais aucune près des points chauds")
        return None

    # Le couvert se juge sur la zone détectée, pas sur toute la fenêtre lue.
    dans_detection = rasterio.features.geometry_mask(
        proches.to_crs(crs).geometry, out_shape=dnbr.shape, transform=transform, invert=True)
    echantillon = nbr_pre[dans_detection & exploitable]
    nbr_avant = float(np.median(echantillon)) if echantillon.size else 0.0
    if nbr_avant < NBR_VEGETATION_MIN:
        print(f"     écarté : NBR avant feu {nbr_avant:.2f} < {NBR_VEGETATION_MIN} "
              "— sol nu ou culture, pas un feu de végétation")
        ecarter(f"couvert végétal insuffisant avant le feu (NBR {nbr_avant:.2f}) — chaume, "
                "sol nu ou site industriel, pas un feu de végétation")
        return None

    surface = float(proches.area.sum() / 1e4)
    dans_zone = rasterio.features.geometry_mask(
        [gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).iloc[0]],
        out_shape=dnbr.shape, transform=transform, invert=True)
    part_masquee = float(1 - (exploitable & dans_zone).sum() / max(dans_zone.sum(), 1))

    dossier = sortie / identifiant
    dossier.mkdir(parents=True, exist_ok=True)
    # Une mesure remplace une estimation antérieure du même feu.
    (dossier / "emprise.geojson").unlink(missing_ok=True)

    info = {
        "id": identifiant,
        "feu": etiquette,
        "statut": "mesure",
        "commune": lieu["commune"],
        "departement": lieu["departement"],
        "surface_ha": round(surface, 1),
        "n_polygones": len(proches),
        # str() explicite : relus depuis un GeoJSON, ces champs reviennent en Timestamp,
        # que json.dumps refuse.
        "premier_point_chaud": f"{debut:%Y-%m-%d}",
        "dernier_point_chaud": dernier,
        "n_points_chauds": int(foyer["n_points"]),
        "frp_total": float(foyer["frp_total"]) if foyer.get("frp_total") is not None else None,
        "image_avant": {"id": it_pre.id, "date": f"{it_pre.datetime:%Y-%m-%d}",
                        "nuages_pct": it_pre.properties.get("eo:cloud_cover")},
        "image_apres": {"id": it_post.id, "date": f"{it_post.datetime:%Y-%m-%d}",
                        "nuages_pct": it_post.properties.get("eo:cloud_cover")},
        "tuile": tuile,
        "seuil_dnbr": seuil,
        "nbr_avant_median": round(nbr_avant, 3),
        "part_masquee": round(part_masquee, 3),
        "latence_jours": (it_post.datetime.date() - debut.date()).days,
        "calcule_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (dossier / "info.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")

    sortie_polys = proches.copy()
    sortie_polys["surface_ha"] = (sortie_polys.area / 1e4).round(2)
    sortie_polys = sortie_polys.set_geometry(
        sortie_polys.simplify(SIMPLIFICATION_PERIMETRE))
    sortie_polys.to_crs(4326).to_file(dossier / "perimetre.geojson", driver="GeoJSON")

    print(f"     → {surface:.1f} ha, {len(proches)} polygones, "
          f"{part_masquee:.0%} masqué, latence {info['latence_jours']} j")
    return info


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jours", type=int, default=5, help="fenêtre FIRMS (1-10)")
    p.add_argument("--min-points", type=int, default=5,
                   help="ignorer les foyers sous ce nombre de points chauds")
    p.add_argument("--max-foyers", type=int, default=10,
                   help="nombre de foyers traités, du plus intense au moins intense")
    p.add_argument("--seuil", type=float, default=SEUIL_DEFAUT)
    p.add_argument("--foyers", type=Path, help="réutiliser un fichier de foyers déjà calculé")
    p.add_argument("--reprise-jours", type=int, default=JOURS_REPRISE,
                   help="réexaminer les feux en attente dont la chaleur a moins de N jours")
    p.add_argument("--sans-reprise", action="store_true",
                   help="s'en tenir aux foyers FIRMS du jour")
    p.add_argument("--out", type=Path, default=Path("data/feux"))
    args = p.parse_args()

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    if not any(abs(s - args.seuil) < 1e-9 for s in SEUILS):
        print(f"  (seuil {args.seuil} hors de la grille de référence {SEUILS})")

    pts = None
    if args.foyers and args.foyers.exists():
        f = gpd.read_file(args.foyers).to_crs(CRS_METRIQUE)
        print(f"  {len(f)} foyers relus depuis {args.foyers}")
        chemin_pts = args.foyers.with_name("foyers_points.geojson")
        if chemin_pts.exists():
            pts = gpd.read_file(chemin_pts).to_crs(CRS_METRIQUE)
    else:
        pts = firms.points_chauds(jours=args.jours)
        print(f"  {len(pts)} points chauds sur {args.jours} j")
        f = firms.foyers(pts)

    f = f[f["n_points"] >= args.min_points]
    f = f.sort_values("frp_total", ascending=False).head(args.max_foyers)
    print(f"  {len(f)} foyers à traiter (≥ {args.min_points} points chauds)")

    client = Client.open(STAC)
    resultats = []
    for _, foyer in f.iterrows():
        try:
            proches = None
            if pts is not None:
                proches = pts[pts.geometry.within(foyer.geometry)]
            info = traiter(foyer, client, args.seuil, args.out, proches)
        except Exception as exc:  # un foyer qui échoue ne doit pas arrêter la chaîne
            print(f"     ÉCHEC : {type(exc).__name__} — {exc}")
            continue
        if info:
            resultats.append(info)

    # Les feux qui n'ont plus de point chaud récent ne sont plus portés par aucun foyer :
    # sans cette reprise, celui qui attendait une image l'attendrait pour toujours, y
    # compris quand elle est publiée depuis des jours.
    if not args.sans_reprise:
        attente = foyers_en_attente(args.out, {r["id"] for r in resultats},
                                    args.reprise_jours)
        garde = attente.head(args.max_foyers)
        if len(attente):
            print(f"\n  reprise : {len(garde)} feux en attente d'image, hors fenêtre FIRMS "
                  f"(chaleur de moins de {args.reprise_jours} j)")
        if len(attente) > len(garde):
            print(f"  ({len(attente) - len(garde)} autres laissés pour la prochaine "
                  f"exécution, --max-foyers={args.max_foyers})")
        for _, foyer in garde.iterrows():
            try:
                info = traiter(foyer, client, args.seuil, args.out)
            except Exception as exc:
                print(f"     ÉCHEC : {type(exc).__name__} — {exc}")
                continue
            if info:
                resultats.append(info)

    mesures = [r for r in resultats if r.get("statut") == "mesure"]
    attentes = [r for r in resultats if r.get("statut") == "en_attente"]
    print(f"\n  {len(mesures)} périmètres mesurés, {len(attentes)} feux en attente d'image "
          f"sur {len(f)} foyers")
    for r in sorted(mesures, key=lambda x: -x["surface_ha"]):
        print(f"    {r['feu'][:34]:36s} {r['surface_ha']:>9.1f} ha  "
              f"(image {r['image_apres']['date']}, latence {r['latence_jours']} j)")
    for r in sorted(attentes, key=lambda x: -x["surface_estimee_ha"]):
        print(f"    {r['feu'][:34]:36s} ~{r['surface_estimee_ha']:>8.0f} ha estimés  "
              f"(en attente, prochain passage {r.get('prochain_passage') or '?'})")
    if resultats:
        print(f"\n  → {args.out}/   puis : uv run python scripts/site.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
