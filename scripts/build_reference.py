"""Assemble les périmètres EMS en une couche de référence unique pour la Phase 0.

Chaque produit EMS (FEP / DEL / DEL_MONITxx / GRA) est un état daté d'un feu en cours,
pas un périmètre final. On les conserve tous, fusionnés par produit, avec la date de
situation et le capteur d'origine : c'est ce qui permet de choisir, pour chaque cas
d'étude, la vérité comparable à une acquisition Sentinel-2 donnée.

Sortie : data/reference/perimetres_ems_2022.geojson (EPSG:4326)
Prérequis : scripts/fetch_ems.py puis scripts/ems_context.py.
"""

import argparse
import csv
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

# Un AOI EMS = un feu. Les libellés viennent des titres de carte.
FEUX = {
    ("EMSR592", "AOI01"): "Landiras (juillet)",
    ("EMSR592", "AOI02"): "La Teste-de-Buch",
    ("EMSR619", "AOI01"): "Landiras (août)",
    ("EMSR633", "AOI01"): "Saumos",
}

# Surface calculée en Lambert-93 : projection conforme officielle pour la métropole.
CRS_METRIQUE = 2154


def charger_contexte(path: Path) -> dict:
    if not path.exists():
        print(f"  (contexte absent : {path} — lancer scripts/ems_context.py)")
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["produit"]: r for r in csv.DictReader(fh)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="data/reference/ems", type=Path)
    parser.add_argument("--contexte", default="data/reference/ems_contexte.csv", type=Path)
    parser.add_argument("--out", default="data/reference/perimetres_ems_2022.geojson", type=Path)
    parser.add_argument("--out-aoi", default="data/reference/aoi_ems_2022.geojson", type=Path)
    args = parser.parse_args()

    contexte = charger_contexte(args.contexte)
    lignes = []
    aois = []

    for z in sorted(args.src.glob("*/*.zip")):
        with zipfile.ZipFile(z) as zf:
            noms = zf.namelist()
        inner = [n for n in noms if n.endswith("observedEventA_r1_v1.shp")]

        # L'emprise analysée par EMS borne toute comparaison : hors AOI, EMS n'a rien
        # cartographié, donc une détection dNBR n'y est ni vraie ni fausse. Sans ce
        # masque, l'IoU serait mécaniquement pénalisé.
        emprise = [n for n in noms if n.endswith("areaOfInterestA_r1_v1.shp")]
        if emprise:
            ga = gpd.read_file(f"zip://{z}!{emprise[0]}").to_crs(CRS_METRIQUE)
            produit_aoi = z.name.removesuffix("_vector.zip")
            act, aoi_id = produit_aoi.split("_")[0], produit_aoi.split("_")[1]
            aois.append({
                "feu": FEUX.get((act, aoi_id), f"{act}/{aoi_id}"),
                "activation": act,
                "aoi": aoi_id,
                "produit": produit_aoi,
                "surface_ha": round(ga.union_all().area / 1e4, 1),
                "geometry": ga.union_all(),
            })

        if not inner:
            print(f"  ignoré (pas de observedEventA) : {z.name}")
            continue

        produit = z.name.removesuffix("_vector.zip")
        activation, aoi, type_prod = produit.split("_")[0], produit.split("_")[1], produit.split("_")[2]

        g = gpd.read_file(f"zip://{z}!{inner[0]}")
        # Un produit peut mélanger plusieurs images source (dmg_src_id) : on garde
        # l'information, mais la géométrie publiée est l'union du produit entier,
        # qui est ce que la carte EMS affiche comme « burnt area ».
        src_ids = sorted(str(v) for v in g["dmg_src_id"].unique())
        geom = g.to_crs(CRS_METRIQUE).union_all()

        ctx = contexte.get(produit) or contexte.get(produit.replace("VECTORS_v1", "RTP01_v2"), {})
        lignes.append({
            "feu": FEUX.get((activation, aoi), f"{activation}/{aoi}"),
            "activation": activation,
            "aoi": aoi,
            "type_produit": type_prod,
            "produit": produit,
            "date_situation": ctx.get("situation", ""),
            "capteur_post": ctx.get("image_post_capteur", ""),
            "date_image_post": ctx.get("image_post_dates", ""),
            "echelle_analyse": ctx.get("echelle", ""),
            "mmu": ctx.get("mmu", ""),
            "reserves": ctx.get("reserves", ""),
            "n_polygones": len(g),
            "dmg_src_id": ",".join(src_ids),
            "surface_ha": round(geom.area / 1e4, 1),
            "geometry": geom,
        })

    if not lignes:
        print("Aucun périmètre trouvé.")
        return 1

    gdf = gpd.GeoDataFrame(lignes, geometry="geometry", crs=CRS_METRIQUE).to_crs(4326)
    gdf = gdf.sort_values(["feu", "date_situation", "type_produit"]).reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(args.out, driver="GeoJSON")

    cols = ["feu", "type_produit", "date_situation", "capteur_post", "surface_ha", "n_polygones"]
    print(gdf[cols].to_string(index=False))
    print(f"\n{len(gdf)} périmètres → {args.out}")

    if aois:
        gaoi = gpd.GeoDataFrame(aois, geometry="geometry", crs=CRS_METRIQUE).to_crs(4326)
        gaoi.to_file(args.out_aoi, driver="GeoJSON")
        print(f"{len(gaoi)} emprises d'analyse → {args.out_aoi}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
