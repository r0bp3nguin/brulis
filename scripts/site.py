"""Construit le site public statique à partir des feux détectés (`data/feux/`).

Sortie : `site/` — hébergeable tel quel, aucun serveur, aucune base.

Deux traitements que les données brutes imposent :

  - **Fusion des doublons.** Un même incendie peut produire plusieurs foyers VIIRS
    distincts (points chauds séparés de plus de 2 km) et donc être détecté deux fois.
    Constaté le 24/07/2026 : « Correns » et « Cotignac » désignaient le même feu, avec
    380 ha d'intersection. Publier les deux doublerait la surface annoncée.
  - **Séparation périmètre / détections isolées.** Le polygone principal et ce qui
    l'entoure forment le feu ; le reste est publié à part, car ce sont souvent des coupes
    forestières. On les montre plutôt que de les écarter en silence.

Usage :
    python scripts/site.py
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

import historique

CRS_METRIQUE = 2154
DISTANCE_REGROUPEMENT = 2000  # m — au-delà, on ne parle plus du même incendie
TOLERANCE_WEB = 10            # m — simplification des contours pour l'affichage
RECOUVREMENT_DOUBLON = 0.3    # part de la plus petite surface au-delà de laquelle
                              # deux détections sont le même feu


def separer(polys: gpd.GeoDataFrame):
    """(périmètre du feu, détections isolées)."""
    if polys.empty:
        return polys, polys
    principal = polys.geometry.iloc[polys.area.values.argmax()]
    proche = polys.geometry.distance(principal) <= DISTANCE_REGROUPEMENT
    return polys[proche], polys[~proche]


def charger(dossier: Path) -> dict | None:
    info_path = dossier / "info.json"
    if not info_path.exists():
        return None
    info = json.loads(info_path.read_text(encoding="utf-8"))
    # Un feu mesuré a un périmètre ; un feu en attente n'a que l'emprise de ses points
    # chauds. Les deux sont publiés, mais jamais présentés de la même façon.
    geo_path = dossier / ("perimetre.geojson" if info.get("statut") != "en_attente"
                          else "emprise.geojson")
    if not geo_path.exists():
        return None
    info["_polys"] = gpd.read_file(geo_path).to_crs(CRS_METRIQUE)
    return info


def fusionner(feux: list[dict]) -> list[dict]:
    """Fusionne les détections qui décrivent le même incendie."""
    feux = sorted(feux, key=lambda f: -f["_polys"].area.sum())
    retenus: list[dict] = []

    for f in feux:
        geom = f["_polys"].union_all()
        for garde in retenus:
            # Défaut explicite : les fiches écrites avant l'ajout du champ n'en ont pas,
            # et sans normalisation elles ne fusionneraient jamais avec les récentes.
            if (garde.get("statut") or "mesure") != (f.get("statut") or "mesure"):
                continue  # un périmètre mesuré et une emprise chaude ne se fusionnent pas
            g = garde["_polys"].union_all()
            inter = geom.intersection(g).area
            if inter > RECOUVREMENT_DOUBLON * min(geom.area, g.area):
                # Concaténer, pas unir : fondre le tout en un seul polygone effacerait
                # la distinction entre le foyer et les détections isolées autour, que
                # `separer` doit encore pouvoir faire.
                garde["_polys"] = gpd.GeoDataFrame(
                    pd.concat([garde["_polys"], f["_polys"]], ignore_index=True),
                    geometry="geometry", crs=CRS_METRIQUE)
                garde["n_points_chauds"] += f["n_points_chauds"]
                garde.setdefault("_fusionnes", []).append(f["feu"])
                garde["premier_point_chaud"] = min(garde["premier_point_chaud"],
                                                   f["premier_point_chaud"])
                garde["dernier_point_chaud"] = max(garde["dernier_point_chaud"],
                                                   f["dernier_point_chaud"])
                print(f"  fusion : « {f['feu']} » rejoint « {garde['feu']} » "
                      f"(même incendie)")
                break
        else:
            retenus.append(f)
    return retenus


def en_feature(info: dict) -> tuple[dict, list]:
    en_attente = info.get("statut") == "en_attente"
    if en_attente:
        # Pas de séparation : l'emprise chaude est un bloc, pas un périmètre à nettoyer.
        feu, isoles = info["_polys"], info["_polys"].iloc[0:0]
    else:
        feu, isoles = separer(info["_polys"])
    surface = float(feu.area.sum() / 1e4)

    reserves = []
    if en_attente:
        reserves.append(info.get("motif_attente", "aucune image exploitable"))
        reserves.append("emprise des points chauds, pas un périmètre brûlé mesuré")
    if info.get("part_masquee", 0) > 0.05:
        reserves.append(
            f"{info['part_masquee']:.0%} de la zone masquée par les nuages ou la fumée")
    if len(isoles):
        reserves.append(f"{len(isoles)} détections isolées écartées du périmètre")
    if info.get("_fusionnes"):
        reserves.append("détection fusionnée avec : " + ", ".join(info["_fusionnes"]))

    props = {
        "id": info["id"],
        "feu": info["feu"],
        "statut": info.get("statut", "mesure"),
        "commune": info.get("commune", ""),
        "departement": info.get("departement", ""),
        "surface_ha": round(surface, 1),
        "debut": info["premier_point_chaud"],
        "fin": info["dernier_point_chaud"],
        "n_points_chauds": info["n_points_chauds"],
        "image_avant": (info.get("image_avant") or {}).get("date", ""),
        "image_apres": (info.get("image_apres") or {}).get("date", ""),
        "latence_jours": info.get("latence_jours"),
        "seuil_dnbr": info.get("seuil_dnbr"),
        "part_masquee": info.get("part_masquee"),
        "surface_min_ha": info.get("surface_min_ha"),
        "surface_max_ha": info.get("surface_max_ha"),
        "prochain_passage": info.get("prochain_passage"),
        "motif_attente": info.get("motif_attente"),
        "reserves": reserves,
        "calcule_le": info.get("calcule_le", ""),
    }

    geom = feu.dissolve().simplify(TOLERANCE_WEB).to_crs(4326).iloc[0]
    trait = {"type": "Feature", "properties": props,
             "geometry": json.loads(gpd.GeoSeries([geom], crs=4326).to_json())
             ["features"][0]["geometry"]}

    isoles_feats = []
    if len(isoles):
        gi = isoles.copy()
        gi["surface_ha"] = (gi.area / 1e4).round(2)
        gi["feu"] = info["feu"]
        gi = gi.set_geometry(gi.simplify(TOLERANCE_WEB)).to_crs(4326)
        isoles_feats = json.loads(
            gi[["feu", "surface_ha", "geometry"]].to_json())["features"]
    return trait, isoles_feats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feux", type=Path, default=Path("data/feux"))
    p.add_argument("--points-chauds", type=Path,
                   default=Path("data/work/foyers_points.geojson"))
    p.add_argument("--gabarit", type=Path, default=Path("scripts/gabarit.html"))
    p.add_argument("--out", type=Path, default=Path("site"))
    args = p.parse_args()

    if not args.feux.exists():
        print(f"{args.feux} absent — lancer `make detecter` d'abord.")
        return 1

    charges = [c for d in sorted(args.feux.iterdir()) if d.is_dir()
               for c in [charger(d)] if c]
    if not charges:
        print(f"Aucun feu dans {args.feux} — lancer `make detecter`.")
        return 1

    feux_info = fusionner(charges)
    feux, isolees = [], []
    for info in feux_info:
        trait, isol = en_feature(info)
        feux.append(trait)
        isolees.extend(isol)

    # Deux zones distinctes d'une même commune portent le même nom : sans distinction,
    # la liste affiche deux fois « Saint-Égrève (38) » sans que rien ne les sépare.
    # On les repère par leur position relative, ce qui parle à qui connaît le terrain.
    par_nom: dict[str, list] = {}
    for f in feux:
        par_nom.setdefault(f["properties"]["feu"], []).append(f)
    for nom, groupe in par_nom.items():
        if len(groupe) < 2:
            continue
        centres = [gpd.GeoSeries([shape(f["geometry"])], crs=4326).to_crs(CRS_METRIQUE)
                   .iloc[0].centroid for f in groupe]
        cy = sum(c.y for c in centres) / len(centres)
        cx = sum(c.x for c in centres) / len(centres)
        for f, c in zip(groupe, centres):
            dy, dx = c.y - cy, c.x - cx
            cote = ("nord" if dy > 0 else "sud") if abs(dy) >= abs(dx) else \
                   ("est" if dx > 0 else "ouest")
            f["properties"]["feu"] = f"{nom} — {cote}"

    feux.sort(key=lambda f: (f["properties"]["statut"] != "en_attente",
                             -f["properties"]["surface_ha"]))

    chauds = {"type": "FeatureCollection", "features": []}
    if args.points_chauds.exists():
        g = gpd.read_file(args.points_chauds).to_crs(4326)
        garder = [c for c in ("acq_date", "frp", "capteur") if c in g.columns]
        g = g[garder + ["geometry"]].copy()
        # Relues depuis un GeoJSON, les dates reviennent en Timestamp, que json refuse.
        for col in garder:
            if g[col].dtype == "object" or "datetime" in str(g[col].dtype):
                g[col] = g[col].astype(str).str.slice(0, 10)
        chauds = json.loads(g.to_json())

    donnees = args.out / "data"
    donnees.mkdir(parents=True, exist_ok=True)
    fc_feux = {"type": "FeatureCollection", "features": feux}
    fc_isol = {"type": "FeatureCollection", "features": isolees}

    (donnees / "feux.geojson").write_text(json.dumps(fc_feux), encoding="utf-8")
    (donnees / "isolees.geojson").write_text(json.dumps(fc_isol), encoding="utf-8")
    (donnees / "points_chauds.geojson").write_text(json.dumps(chauds), encoding="utf-8")

    colonnes = ["id", "feu", "statut", "commune", "departement", "surface_ha",
                "surface_min_ha", "surface_max_ha", "debut", "fin", "n_points_chauds",
                "image_avant", "image_apres", "latence_jours", "seuil_dnbr",
                "part_masquee", "prochain_passage", "calcule_le"]
    with (donnees / "feux.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colonnes, extrasaction="ignore")
        w.writeheader()
        w.writerows(f["properties"] for f in feux)

    # Journal des changements : sans mémoire, impossible de dire « nouveau » ou
    # « agrandi », qui est l'information utile à qui revient consulter la carte.
    genere = historique.maintenant()
    journal = historique.mettre_a_jour(donnees / "historique.json", feux, genere)

    html = (args.gabarit.read_text(encoding="utf-8")
            .replace("__FEUX__", json.dumps(fc_feux, ensure_ascii=False))
            .replace("__ISOLEES__", json.dumps(fc_isol, ensure_ascii=False))
            .replace("__CHAUDS__", json.dumps(chauds, ensure_ascii=False))
            .replace("__JOURNAL__", json.dumps(
                {"evenements": journal["evenements"]}, ensure_ascii=False))
            .replace("__GENERE__", genere))
    (args.out / "index.html").write_text(html, encoding="utf-8")

    mesures = [f for f in feux if f["properties"]["statut"] == "mesure"]
    total = sum(f["properties"]["surface_ha"] for f in mesures)
    print()
    for f in feux:
        pr = f["properties"]
        if pr["statut"] == "en_attente":
            print(f"  {pr['feu'][:34]:36s} {pr['surface_min_ha']:>6.0f}–"
                  f"{pr['surface_max_ha']:.0f} ha  EN ATTENTE "
                  f"(passage {pr.get('prochain_passage') or '?'})")
        else:
            print(f"  {pr['feu'][:34]:36s} {pr['surface_ha']:>9.1f} ha  "
                  f"image {pr['image_apres']}  latence {pr['latence_jours']} j")
    poids = sum(x.stat().st_size for x in args.out.rglob("*") if x.is_file()) / 1e6
    print(f"\n  {len(mesures)} feux mesurés ({total:.0f} ha), "
          f"{len(feux) - len(mesures)} en attente d'image, "
          f"{len(isolees)} détections isolées, {len(chauds['features'])} points chauds")
    print(f"  → {args.out}/  ({poids:.1f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
