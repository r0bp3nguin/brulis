"""Génère un projet QGIS avec les couches de vérification déjà chargées et stylées.

Sert l'inspection fine : zoomer sur un faux positif, basculer entre dNBR et fond
satellite, mesurer une parcelle. Complète les planches PNG de `apercu.py`, qui elles
figent une trace dans le dépôt.

Le projet est écrit en `.qgs` (XML non compressé, lisible et diffable) avec des chemins
**relatifs** : le dossier `data/work/` reste déplaçable.

Ordre des couches, du dessus vers le dessous :
    périmètres EMS (rouge, contour)  ·  détections (bleu, contour)
    emprises d'analyse (gris, tirets) ·  dNBR (niveaux de gris)

⚠️ QGIS n'étant pas installé sur cette machine, ce fichier n'a pas pu être ouvert pour
vérification. Le format est celui de QGIS 3.x. En cas de problème, les couches restent
chargeables une à une (glisser-déposer des .geojson et .tif) — le style seul serait perdu.

Usage :
    python scripts/projet_qgis.py
    open data/work/brulis.qgs
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

CRS_PROJET = 2154  # Lambert-93

STYLE_EMS = {"trait": "214,39,40,255", "epaisseur": "0.66"}
STYLE_DETECTION = {"trait": "31,119,180,255", "epaisseur": "0.4"}
STYLE_AOI = {"trait": "120,120,120,255", "epaisseur": "0.3", "tirets": True}


def chemin_relatif(chemin: Path, racine: Path) -> str:
    """Chemin relatif au projet ; les couches de référence sont hors de data/work,
    donc un ../ est nécessaire — Path.relative_to ne sait pas le produire."""
    rel = os.path.relpath(chemin, racine).replace(os.sep, "/")
    return rel if rel.startswith(".") else f"./{rel}"


def _options(parent, valeurs: dict):
    opt = ET.SubElement(parent, "Option", {"type": "Map"})
    for k, v in valeurs.items():
        ET.SubElement(opt, "Option", {"name": k, "type": "QString", "value": v})


def renderer_contour(parent, trait: str, epaisseur: str, tirets: bool = False):
    """Symbole de remplissage transparent, contour coloré : ne masque jamais le fond."""
    rend = ET.SubElement(parent, "renderer-v2", {
        "type": "singleSymbol", "symbollevels": "0",
        "forceraster": "0", "enableorderby": "0",
    })
    symbols = ET.SubElement(rend, "symbols")
    sym = ET.SubElement(symbols, "symbol", {
        "type": "fill", "name": "0", "alpha": "1",
        "clip_to_extent": "1", "force_rhr": "0",
    })
    couche = ET.SubElement(sym, "layer", {
        "class": "SimpleFill", "locked": "0", "pass": "0", "enabled": "1",
    })
    _options(couche, {
        "color": "0,0,0,0",
        "style": "no",
        "outline_color": trait,
        "outline_width": epaisseur,
        "outline_width_unit": "MM",
        "outline_style": "dash" if tirets else "solid",
        "joinstyle": "round",
    })


def couche_vecteur(parent_layers, arbre, chemin: Path, racine: Path, nom: str,
                   style: dict, visible: bool = True):
    rel = chemin_relatif(chemin, racine)
    ident = f"{nom}_{abs(hash(rel)) % 10**10}".replace(" ", "_").replace("—", "-")

    ET.SubElement(arbre, "layer-tree-layer", {
        "id": ident, "name": nom, "source": rel,
        "providerKey": "ogr", "checked": "Qt::Checked" if visible else "Qt::Unchecked",
        "expanded": "0",
    })

    ml = ET.SubElement(parent_layers, "maplayer", {
        "type": "vector", "geometry": "Polygon", "hasScaleBasedVisibilityFlag": "0",
    })
    ET.SubElement(ml, "id").text = ident
    ET.SubElement(ml, "datasource").text = rel
    ET.SubElement(ml, "layername").text = nom
    ET.SubElement(ml, "provider", {"encoding": "UTF-8"}).text = "ogr"
    srs = ET.SubElement(ET.SubElement(ml, "srs"), "spatialrefsys")
    ET.SubElement(srs, "authid").text = "EPSG:4326"
    renderer_contour(ml, style["trait"], style["epaisseur"], style.get("tirets", False))
    ET.SubElement(ml, "blendMode").text = "0"


def couche_raster(parent_layers, arbre, chemin: Path, racine: Path, nom: str,
                  visible: bool = False):
    rel = chemin_relatif(chemin, racine)
    ident = f"{nom}_{abs(hash(rel)) % 10**10}".replace(" ", "_").replace("—", "-")

    ET.SubElement(arbre, "layer-tree-layer", {
        "id": ident, "name": nom, "source": rel,
        "providerKey": "gdal", "checked": "Qt::Checked" if visible else "Qt::Unchecked",
        "expanded": "0",
    })

    ml = ET.SubElement(parent_layers, "maplayer", {
        "type": "raster", "hasScaleBasedVisibilityFlag": "0",
    })
    ET.SubElement(ml, "id").text = ident
    ET.SubElement(ml, "datasource").text = rel
    ET.SubElement(ml, "layername").text = nom
    ET.SubElement(ml, "provider").text = "gdal"
    rend = ET.SubElement(ml, "rasterrenderer", {
        "type": "singlebandgray", "band": "1", "gradient": "BlackToWhite",
        "opacity": "1", "alphaBand": "-1",
    })
    ET.SubElement(rend, "contrastEnhancement")
    ET.SubElement(ml, "blendMode").text = "0"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", type=Path, default=Path("data/work"))
    p.add_argument("--reference", type=Path, default=Path("data/reference"))
    p.add_argument("--out", type=Path, default=Path("data/work/brulis.qgs"))
    args = p.parse_args()

    racine = args.out.parent.resolve()
    qgis = ET.Element("qgis", {"version": "3.34.0", "projectname": "Brûlis — vérification Phase 0"})
    ET.SubElement(qgis, "homePath", {"path": ""})
    ET.SubElement(qgis, "title").text = "Brûlis — vérification Phase 0"
    srs = ET.SubElement(ET.SubElement(qgis, "projectCrs"), "spatialrefsys")
    ET.SubElement(srs, "authid").text = f"EPSG:{CRS_PROJET}"

    arbre = ET.SubElement(qgis, "layer-tree-group")
    layers = ET.SubElement(qgis, "projectlayers")

    n = 0
    # Vérité et emprises en premier : elles restent au-dessus dans l'arbre QGIS.
    perimetres = (args.reference / "perimetres_ems_2022.geojson").resolve()
    if perimetres.exists():
        couche_vecteur(layers, arbre, perimetres, racine, "Périmètres EMS (vérité)", STYLE_EMS)
        n += 1

    for dossier in sorted(d for d in args.work.iterdir() if d.is_dir()):
        polys = (dossier / "polygones.geojson").resolve()
        if polys.exists():
            couche_vecteur(layers, arbre, polys, racine,
                           f"Détections — {dossier.name[:40]}", STYLE_DETECTION)
            n += 1

    aoi = (args.reference / "aoi_ems_2022.geojson").resolve()
    if aoi.exists():
        couche_vecteur(layers, arbre, aoi, racine, "Emprises d'analyse EMS", STYLE_AOI,
                       visible=False)
        n += 1

    for dossier in sorted(d for d in args.work.iterdir() if d.is_dir()):
        tif = (dossier / "dnbr.tif").resolve()
        if tif.exists():
            couche_raster(layers, arbre, tif, racine, f"dNBR — {dossier.name[:40]}")
            n += 1

    if n == 0:
        print("Aucune couche à charger — lancer scripts/dnbr.py d'abord.")
        return 1

    ET.SubElement(arbre, "custom-order", {"enabled": "0"})
    xml = minidom.parseString(ET.tostring(qgis, encoding="unicode")).toprettyxml(indent="  ")
    xml = xml.replace(
        '<?xml version="1.0" ?>',
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>",
    )
    args.out.write_text(xml, encoding="utf-8")

    print(f"  {n} couches → {args.out}")
    print("  rasters dNBR décochés par défaut (à activer un par un)")
    print("  ⚠️ non ouvert pour vérification : QGIS n'est pas installé ici")
    print(f"\n  brew install --cask qgis && open {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
