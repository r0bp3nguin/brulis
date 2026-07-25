"""Recense les détections situées hors du périmètre de référence (point 4 du PLAN).

Un IoU global ne dit pas *où* la méthode se trompe. Ce script sépare les polygones
détectés en deux familles :

  - **sur le feu** : ceux qui touchent le périmètre EMS — le feu lui-même ;
  - **hors feu**   : ceux qui n'y touchent pas — faux positifs candidats.

Pour les seconds, il calcule un indice de compacité de Polsby-Popper
(4·π·aire / périmètre²) : proche de 1 = forme ronde/compacte, proche de 0 = allongée.
Une coupe forestière ou une parcelle agricole est un rectangle net, aux bords alignés
sur le parcellaire ; un feu a des contours irréguliers. La compacité seule ne suffit pas
à trancher, mais elle chiffre ce que la vérification visuelle donne à voir.

Sortie : data/work/faux_positifs.csv + résumé sur stdout.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd

CRS_METRIQUE = 2154


def compacite(geom) -> float:
    """Polsby-Popper : 1 = disque, 0 = filiforme."""
    if geom.length == 0:
        return 0.0
    return 4 * math.pi * geom.area / (geom.length ** 2)


def analyser(dossier: Path, ref: gpd.GeoDataFrame) -> dict | None:
    polys_path = dossier / "polygones.geojson"
    if not polys_path.exists():
        return None
    metriques = json.loads((dossier / "metriques.json").read_text(encoding="utf-8"))

    polys = gpd.read_file(polys_path).to_crs(CRS_METRIQUE)
    verite = ref[ref["produit"] == metriques["produit_verite"]].to_crs(CRS_METRIQUE)
    verite_geom = verite.union_all()

    # « Touche le feu » : intersection non vide. Un polygone à cheval sur la limite
    # compte comme feu, pas comme faux positif — on ne pénalise pas un contour flou.
    touche = polys.intersects(verite_geom)
    hors = polys[~touche].copy()
    sur = polys[touche]

    hors["surface_ha"] = hors.area / 1e4
    hors["compacite"] = hors.geometry.map(compacite)

    aire_hors = hors.area.sum() / 1e4
    aire_totale = polys.area.sum() / 1e4

    return {
        "cas": dossier.name,
        "feu": metriques["feu"],
        "seuil": metriques.get("seuil_retenu"),
        "n_total": len(polys),
        "n_sur_feu": int(touche.sum()),
        "n_hors_feu": len(hors),
        "surface_totale_ha": round(aire_totale, 1),
        "surface_sur_feu_ha": round(sur.area.sum() / 1e4, 1),
        "surface_hors_feu_ha": round(aire_hors, 1),
        "part_hors_feu_pct": round(100 * aire_hors / aire_totale, 1) if aire_totale else 0,
        "mediane_surface_hors_ha": round(hors["surface_ha"].median(), 2) if len(hors) else None,
        "max_surface_hors_ha": round(hors["surface_ha"].max(), 1) if len(hors) else None,
        "mediane_compacite_hors": round(hors["compacite"].median(), 3) if len(hors) else None,
        "mediane_compacite_sur": round(
            sur.geometry.map(compacite).median(), 3) if len(sur) else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", type=Path, default=Path("data/work"))
    p.add_argument("--ref", type=Path,
                   default=Path("data/reference/perimetres_ems_2022.geojson"))
    p.add_argument("--out", type=Path, default=Path("data/work/faux_positifs.csv"))
    args = p.parse_args()

    ref = gpd.read_file(args.ref)
    lignes = [r for d in sorted(args.work.iterdir()) if d.is_dir()
              for r in [analyser(d, ref)] if r]
    if not lignes:
        print("Aucun cas exploitable — lancer scripts/dnbr.py d'abord.")
        return 1

    print(f"{'feu':22s} {'seuil':>5} {'n hors':>7} {'ha hors':>9} {'% du détecté':>13} "
          f"{'ha médian':>10} {'compac. hors/sur':>18}")
    for r in lignes:
        print(f"{r['feu'][:22]:22s} {r['seuil']:>5.2f} {r['n_hors_feu']:>7} "
              f"{r['surface_hors_feu_ha']:>9.1f} {r['part_hors_feu_pct']:>12.1f}% "
              f"{r['mediane_surface_hors_ha'] or 0:>10.2f} "
              f"{str(r['mediane_compacite_hors']) + ' / ' + str(r['mediane_compacite_sur']):>18}")

    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(lignes[0]))
        w.writeheader()
        w.writerows(lignes)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
