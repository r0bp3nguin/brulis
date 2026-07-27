"""Planche de vérification visuelle pour un cas traité par `dnbr.py`.

Objectif : pouvoir *regarder* une détection, pas seulement lire son IoU. Un IoU élevé
peut cohabiter avec des artefacts — une parcelle agricole récoltée entre les deux dates
produit la même signature spectrale qu'un brûlis. Relever ces faux positifs demande
des yeux, pas seulement des métriques.

Deux panneaux côte à côte, même emprise :
  - couleur naturelle après feu (bande `visual` à 10 m) : c'est là qu'on reconnaît une
    parcelle agricole, une coupe forestière ou une ombre de nuage ;
  - dNBR : c'est ce que l'algorithme voit.

Sur les deux : périmètre EMS (référence) et polygones détectés, en contour seulement pour
ne rien cacher.

Usage :
    python scripts/apercu.py                       # tous les cas de data/work
    python scripts/apercu.py --cas <nom_dossier>   # un seul
"""

import argparse
import json
import os
import sys
from pathlib import Path

import env_geo  # noqa: F401  — doit précéder rasterio/GDAL
import geopandas as gpd
import matplotlib
import numpy as np
import rasterio
from pystac_client import Client

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

COULEUR_EMS = "#d62728"       # rouge — vérité
COULEUR_DETECTION = "#1f77b4"  # bleu — ce que nous produisons


def couleur_naturelle(item_id: str, bounds, crs_cible):
    """Lit la composition colorée `visual` (10 m) sur l'emprise du dNBR."""
    client = Client.open(STAC)
    got = list(client.search(collections=[COLLECTION], ids=[item_id]).items())
    if not got:
        return None, None
    href = got[0].assets["visual"].href
    with rasterio.open(href) as ds:
        if ds.crs != crs_cible:
            return None, None
        win = ds.window(*bounds).round_offsets().round_lengths()
        arr = ds.read(window=win, boundless=True, fill_value=0)
        etendue = rasterio.windows.bounds(win, ds.transform)
    # (bandes, lignes, colonnes) -> (lignes, colonnes, bandes) pour imshow
    return np.transpose(arr, (1, 2, 0)), etendue


def planche(dossier: Path, sortie: Path | None = None) -> Path | None:
    metriques = json.loads((dossier / "metriques.json").read_text(encoding="utf-8"))
    chemin_polys = dossier / "polygones.geojson"
    if not chemin_polys.exists():
        print(f"  {dossier.name} : polygones.geojson absent — relancer dnbr.py")
        return None

    with rasterio.open(dossier / "dnbr.tif") as ds:
        dnbr = ds.read(1)
        crs = ds.crs
        etendue_dnbr = (ds.bounds.left, ds.bounds.right, ds.bounds.bottom, ds.bounds.top)
        bounds = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)

    detections = gpd.read_file(chemin_polys).to_crs(crs)
    verite = gpd.read_file("data/reference/perimetres_ems_2022.geojson")
    verite = verite[verite["produit"] == metriques["produit_verite"]].to_crs(crs)

    rgb, etendue_rgb = couleur_naturelle(metriques["image_apres"]["id"], bounds, crs)

    fig, axes = plt.subplots(1, 2, figsize=(16, 8.2), constrained_layout=True)

    if rgb is not None:
        axes[0].imshow(rgb, extent=(etendue_rgb[0], etendue_rgb[2],
                                    etendue_rgb[1], etendue_rgb[3]))
        axes[0].set_title(f"Couleur naturelle — après feu, {metriques['image_apres']['date']}",
                          fontsize=11)
    else:
        axes[0].text(0.5, 0.5, "composition colorée indisponible",
                     ha="center", va="center", transform=axes[0].transAxes)

    im = axes[1].imshow(dnbr, extent=etendue_dnbr, cmap="inferno", vmin=-0.1, vmax=0.8)
    axes[1].set_title("dNBR (ce que voit l'algorithme)", fontsize=11)
    fig.colorbar(im, ax=axes[1], shrink=0.72, label="dNBR")

    for ax in axes:
        verite.boundary.plot(ax=ax, color=COULEUR_EMS, linewidth=1.5)
        detections.boundary.plot(ax=ax, color=COULEUR_DETECTION, linewidth=0.9)
        ax.set_xlim(etendue_dnbr[0], etendue_dnbr[1])
        ax.set_ylim(etendue_dnbr[2], etendue_dnbr[3])
        ax.set_xticks([])
        ax.set_yticks([])

    best = metriques["meilleur_seuil"]
    retenu = next(
        (r for r in metriques["resultats"]
         if abs(r["seuil"] - metriques.get("seuil_retenu", best["seuil"])) < 1e-9),
        best,
    )
    axes[0].legend(handles=[
        mpatches.Patch(edgecolor=COULEUR_EMS, facecolor="none",
                       label=f"périmètre EMS — {metriques['surface_verite_ha']:.0f} ha"),
        mpatches.Patch(edgecolor=COULEUR_DETECTION, facecolor="none",
                       label=f"détection dNBR ≥ {retenu['seuil']:.2f} — "
                             f"{retenu['surface_detectee_ha']:.0f} ha"),
    ], loc="upper right", fontsize=9, framealpha=0.9)

    reserves = metriques.get("reserves_ems") or "aucune"
    fig.suptitle(
        f"{metriques['feu']} — vérité EMS {metriques['date_situation_ems']} "
        f"({metriques['capteur_ems']})   ·   "
        f"S2 {metriques['image_avant']['date']} → {metriques['image_apres']['date']}\n"
        f"seuil {retenu['seuil']:.2f} : IoU {retenu['iou']:.3f} · "
        f"rappel {retenu['rappel']:.3f} (plafond nuages {metriques.get('plafond_rappel', 1):.3f}) · "
        f"précision {retenu['precision']:.3f} · "
        f"écart surface {retenu['ecart_surface_pct']:+.1f} %\n"
        f"réserves EMS sur la vérité : {reserves}",
        fontsize=10.5,
    )

    sortie = sortie or dossier / "apercu.png"
    fig.savefig(sortie, dpi=115)
    plt.close(fig)
    return sortie


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", type=Path, default=Path("data/work"))
    p.add_argument("--cas", help="nom du sous-dossier à traiter (défaut : tous)")
    args = p.parse_args()

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    dossiers = ([args.work / args.cas] if args.cas
                else sorted(d for d in args.work.iterdir() if (d / "metriques.json").exists()))
    if not dossiers:
        print(f"Aucun cas dans {args.work} — lancer scripts/dnbr.py d'abord.")
        return 1

    for d in dossiers:
        out = planche(d)
        if out:
            print(f"  {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
