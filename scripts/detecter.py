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
recalculé au passage suivant.

Usage :
    python scripts/detecter.py --jours 5 --max-foyers 10
    python scripts/detecter.py --foyer data/work/foyers.geojson --max-foyers 3
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from pystac_client import Client
from shapely.geometry import box

import firms
from dnbr import COLLECTION, CRS_METRIQUE, SEUILS, STAC, nbr, polygoniser

SEUIL_DEFAUT = 0.15        # optimum mesuré sur pinède landaise (docs/phase0-resultats.md)
SURFACE_MIN_HA = 1.0
MARGE_M = 3000             # marge autour du foyer pour capter le feu au-delà des points chauds
JOURS_AVANT = 60           # fenêtre de recherche de l'image avant-feu
COUVERTURE_MIN = 0.55      # part minimale de pixels exploitables sur la zone
DISTANCE_RATTACHEMENT = 3000  # un polygone au-delà n'est pas rattaché à ce foyer


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


def couverture(it, zone_m, marge=MARGE_M) -> tuple[float, object]:
    """Part de pixels exploitables sur la zone, et données lues (évite un double accès)."""
    crs = rasterio.crs.CRS.from_epsg(epsg_de(it))
    bounds = gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).buffer(marge).total_bounds
    valeurs, valide, transform, _ = nbr(it, bounds)
    if valide.size == 0:
        return 0.0, None
    zone_locale = gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).iloc[0]
    dans = rasterio.features.geometry_mask(
        [zone_locale], out_shape=valide.shape, transform=transform, invert=True)
    if dans.sum() == 0:
        return 0.0, None
    return float((valide & dans).sum() / dans.sum()), (valeurs, valide, transform, crs)


def traiter(foyer, client: Client, seuil: float, sortie: Path) -> dict | None:
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
    etiquette = (f"{lieu['commune']} ({lieu['departement']})" if lieu["commune"]
                 else f"foyer {foyer['foyer']}")
    print(f"\n  {etiquette} — {foyer['n_points']} points chauds, "
          f"{foyer['date_debut']} → {foyer['date_fin']}")

    apres_max = datetime.now(timezone.utc) + timedelta(days=1)
    candidats = sorted(
        client.search(collections=[COLLECTION], bbox=list(bbox),
                      datetime=f"{debut:%Y-%m-%d}/{apres_max:%Y-%m-%d}").item_collection(),
        key=lambda i: i.datetime, reverse=True)
    if not candidats:
        print("     aucune image après le départ de feu")
        return None

    # On teste les plus récentes d'abord et on s'arrête à la première exploitable.
    for it_post in candidats[:6]:
        part, lu = couverture(it_post, zone_m)
        marque = "retenue" if part >= COUVERTURE_MIN else "écartée"
        print(f"     après  {it_post.datetime:%Y-%m-%d} {tuile_de(it_post)} "
              f"exploitable {part:.0%} — {marque}")
        if part >= COUVERTURE_MIN:
            break
    else:
        print("     aucune image après-feu suffisamment dégagée")
        return None

    nbr_post, val_post, transform, crs = lu
    tuile = tuile_de(it_post)

    # Image avant : même tuile obligatoire, la plus récente qui soit claire sur la zone.
    avant = [i for i in sorted(
        client.search(collections=[COLLECTION], bbox=list(bbox),
                      datetime=f"{debut - timedelta(days=JOURS_AVANT):%Y-%m-%d}/"
                               f"{debut:%Y-%m-%d}").item_collection(),
        key=lambda i: i.datetime, reverse=True) if tuile_de(i) == tuile]

    for it_pre in avant[:8]:
        part, lu_pre = couverture(it_pre, zone_m)
        marque = "retenue" if part >= COUVERTURE_MIN else "écartée"
        print(f"     avant  {it_pre.datetime:%Y-%m-%d} exploitable {part:.0%} — {marque}")
        if part >= COUVERTURE_MIN:
            break
    else:
        print("     aucune image avant-feu exploitable sur la même tuile")
        return None

    nbr_pre, val_pre, transform_pre, _ = lu_pre
    if nbr_pre.shape != nbr_post.shape:
        print("     fenêtres de lecture incohérentes — foyer ignoré")
        return None

    exploitable = val_pre & val_post
    dnbr = nbr_pre - nbr_post

    polys = polygoniser(exploitable & (dnbr >= seuil), transform, crs, SURFACE_MIN_HA)
    if polys.empty:
        print("     aucun polygone au-dessus du seuil")
        return None

    # Rattachement au foyer : un brûlis à 20 km n'est pas cet incendie.
    proches = polys[polys.distance(zone_m) <= DISTANCE_RATTACHEMENT]
    if proches.empty:
        print("     polygones trouvés mais aucun près des points chauds")
        return None

    surface = float(proches.area.sum() / 1e4)
    dans_zone = rasterio.features.geometry_mask(
        [gpd.GeoSeries([zone_m], crs=CRS_METRIQUE).to_crs(crs).iloc[0]],
        out_shape=dnbr.shape, transform=transform, invert=True)
    part_masquee = float(1 - (exploitable & dans_zone).sum() / max(dans_zone.sum(), 1))

    identifiant = (f"{debut:%Y%m%d}-{lieu['departement'] or '00'}-"
                   f"{(lieu['commune'] or 'foyer').lower().replace(' ', '-')[:24]}")
    dossier = sortie / identifiant
    dossier.mkdir(parents=True, exist_ok=True)

    info = {
        "id": identifiant,
        "feu": etiquette,
        "commune": lieu["commune"],
        "departement": lieu["departement"],
        "surface_ha": round(surface, 1),
        "n_polygones": len(proches),
        # str() explicite : relus depuis un GeoJSON, ces champs reviennent en Timestamp,
        # que json.dumps refuse.
        "premier_point_chaud": str(foyer["date_debut"])[:10],
        "dernier_point_chaud": str(foyer["date_fin"])[:10],
        "n_points_chauds": int(foyer["n_points"]),
        "frp_total": float(foyer["frp_total"]) if foyer.get("frp_total") is not None else None,
        "image_avant": {"id": it_pre.id, "date": f"{it_pre.datetime:%Y-%m-%d}",
                        "nuages_pct": it_pre.properties.get("eo:cloud_cover")},
        "image_apres": {"id": it_post.id, "date": f"{it_post.datetime:%Y-%m-%d}",
                        "nuages_pct": it_post.properties.get("eo:cloud_cover")},
        "tuile": tuile,
        "seuil_dnbr": seuil,
        "part_masquee": round(part_masquee, 3),
        "latence_jours": (it_post.datetime.date() - debut.date()).days,
        "calcule_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (dossier / "info.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")

    sortie_polys = proches.copy()
    sortie_polys["surface_ha"] = (sortie_polys.area / 1e4).round(2)
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
    p.add_argument("--out", type=Path, default=Path("data/feux"))
    args = p.parse_args()

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    if not any(abs(s - args.seuil) < 1e-9 for s in SEUILS):
        print(f"  (seuil {args.seuil} hors de la grille de référence {SEUILS})")

    if args.foyers and args.foyers.exists():
        f = gpd.read_file(args.foyers).to_crs(CRS_METRIQUE)
        print(f"  {len(f)} foyers relus depuis {args.foyers}")
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
            info = traiter(foyer, client, args.seuil, args.out)
        except Exception as exc:  # un foyer qui échoue ne doit pas arrêter la chaîne
            print(f"     ÉCHEC : {type(exc).__name__} — {exc}")
            continue
        if info:
            resultats.append(info)

    print(f"\n  {len(resultats)} périmètres produits sur {len(f)} foyers")
    for r in sorted(resultats, key=lambda x: -x["surface_ha"]):
        print(f"    {r['feu'][:34]:36s} {r['surface_ha']:>9.1f} ha  "
              f"(image {r['image_apres']['date']}, latence {r['latence_jours']} j)")
    if resultats:
        print(f"\n  → {args.out}/   puis : uv run python scripts/site.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
