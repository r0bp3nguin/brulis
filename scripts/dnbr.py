"""dNBR Sentinel-2 sur un cas d'étude, puis comparaison au périmètre EMS (validation de la méthode).

Méthode (UN-SPIDER, cf. SETUP.md) :
    NBR  = (B8A - B12) / (B8A + B12)          bandes natives 20 m
    dNBR = NBR_avant - NBR_apres
    zone brûlée = dNBR >= seuil, nettoyée par surface minimale

Toute la comparaison est clippée sur l'emprise analysée par EMS (`aoi_ems_2022.geojson`) :
hors de cette emprise EMS n'a rien cartographié, une détection n'y serait ni vraie ni fausse.

Deux points vérifiés empiriquement sur la collection `sentinel-2-l2a` d'Earth Search :
  - accès anonyme (aucun compte CDSE nécessaire pour la Phase 0) ;
  - le décalage radiométrique de la baseline 04.00 est DÉJÀ appliqué aux valeurs
    (l'eau lit B12 ~ 43 DN, soit 0,004 de réflectance, et non ~1043). Le champ
    `raster:bands.offset = -0.1` des métadonnées STAC est obsolète : ne pas le
    ré-appliquer, cela biaiserait le NBR.

Usage :
    python scripts/dnbr.py --produit EMSR633_AOI01_DEL_PRODUCT_r1_RTP01_v1 \
        --pre S2B_30TXQ_20220905_0_L2A --post S2A_30TXQ_20220920_0_L2A
"""

import argparse
import json
import os
import sys
from pathlib import Path

import env_geo  # noqa: F401  — doit précéder rasterio/GDAL (cf. son docstring)
import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from pystac_client import Client
from shapely.geometry import box, shape

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
CRS_METRIQUE = 2154

# Classification de scène (SCL) Sen2Cor. On écarte le sans-donnée, la saturation,
# l'ombre de nuage, les nuages, la neige — et l'eau (6).
# On GARDE 2 (pixels sombres) et 5 (sol nu) : une zone fraîchement brûlée y tombe presque
# toujours — les exclure reviendrait à masquer ce qu'on cherche.
#
# L'eau a d'abord été conservée (raisonnement : NBR bas aux deux dates, donc dNBR ~ 0).
# C'était faux, et la vérification visuelle l'a montré : sur le lac de Cazaux, plusieurs
# centaines d'hectares étaient détectés comme brûlés. En eau, B8A et B12 valent quelques
# dizaines de DN ; le rapport (a-b)/(a+b) y est numériquement instable et bascule d'une
# date à l'autre sur du bruit. Voir REFLECTANCE_MIN pour le garde-fou général.
SCL_INVALIDE = {0, 1, 3, 6, 8, 9, 10, 11}

# Garde-fou de stabilité du rapport, indépendant du SCL (qui rate les eaux peu profondes,
# les ombres denses et les zones humides). En dessous de cette somme de réflectances,
# le NBR n'a pas de sens physique exploitable : quelques DN de bruit suffisent à le faire
# varier de plusieurs dixièmes. Végétation ~0,30, brûlé ~0,35, eau ~0,009.
REFLECTANCE_MIN = 0.05

# Seuils UN-SPIDER : 0,10 = limite brûlé/non brûlé, 0,27 = sévérité faible,
# 0,44 = modérée-basse, 0,66 = modérée-haute.
SEUILS = [0.10, 0.15, 0.20, 0.27, 0.35, 0.44, 0.55, 0.66]


def item(client: Client, item_id: str):
    got = list(client.search(collections=[COLLECTION], ids=[item_id]).items())
    if not got:
        raise SystemExit(f"Item STAC introuvable : {item_id}")
    return got[0]


def lire(it, bounds, cle: str):
    """Lit une bande sur l'emprise voulue, exprimée dans le CRS de la tuile."""
    with rasterio.open(it.assets[cle].href) as ds:
        win = ds.window(*bounds)
        win = win.round_offsets().round_lengths()
        arr = ds.read(1, window=win)
        return arr, ds.window_transform(win), ds.crs


def nbr(it, bounds):
    """NBR + masque de validité pour une acquisition."""
    b8a, transform, crs = lire(it, bounds, "nir08")
    b12, _, _ = lire(it, bounds, "swir22")
    scl, _, _ = lire(it, bounds, "scl")

    # Réflectance = DN * 1e-4 (décalage baseline 04.00 déjà appliqué, cf. en-tête).
    a = b8a.astype("float32") * 1e-4
    b = b12.astype("float32") * 1e-4

    somme = a + b
    valide = (
        ~np.isin(scl, list(SCL_INVALIDE))
        & (b8a > 0)
        & (b12 > 0)
        & (somme >= REFLECTANCE_MIN)
    )
    out = np.full(a.shape, np.nan, dtype="float32")
    np.divide(a - b, somme, out=out, where=valide)
    return out, valide, transform, crs


def polygoniser(masque, transform, crs, surface_min_ha):
    """Masque booléen -> polygones, sans les objets sous la surface minimale."""
    geoms = [
        shape(geom)
        for geom, val in rasterio.features.shapes(
            masque.astype("uint8"), mask=masque, transform=transform
        )
        if val == 1
    ]
    if not geoms:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    g = gpd.GeoDataFrame({"geometry": geoms}, geometry="geometry", crs=crs)
    g = g.to_crs(CRS_METRIQUE)
    return g[g.area >= surface_min_ha * 1e4].reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--produit", required=True,
                        help="produit EMS servant de vérité (colonne 'produit' de la référence)")
    parser.add_argument("--pre", required=True, help="id STAC de l'image avant-feu")
    parser.add_argument("--post", required=True, help="id STAC de l'image après-feu")
    parser.add_argument("--ref", default="data/reference/perimetres_ems_2022.geojson", type=Path)
    parser.add_argument("--aoi", default="data/reference/aoi_ems_2022.geojson", type=Path)
    parser.add_argument("--exclure-produit", action="append", default=[], metavar="PRODUIT",
                        help="périmètre de référence à retirer de la zone de comparaison "
                             "(cicatrice antérieure : un sur-brûlage y est invisible au dNBR). "
                             "Répétable.")
    parser.add_argument("--seuil-retenu", type=float, default=0.15,
                        help="seuil dont les polygones sont exportés (défaut 0,15 : optimum "
                             "d'IoU mesuré en Phase 0 ; 0,20 pour la fidélité de surface). "
                             "Doit figurer dans la liste balayée.")
    parser.add_argument("--surface-min-ha", type=float, default=1.0,
                        help="surface minimale d'un polygone retenu (défaut 1 ha)")
    parser.add_argument("--marge-m", type=float, default=2000,
                        help="marge autour de l'emprise EMS pour la lecture des images")
    parser.add_argument("--out", default="data/work", type=Path)
    args = parser.parse_args()

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    if not any(abs(s - args.seuil_retenu) < 1e-9 for s in SEUILS):
        raise SystemExit(
            f"--seuil-retenu {args.seuil_retenu} absent des seuils balayés {SEUILS} : "
            "aucun polygone ne serait exporté."
        )

    ref = gpd.read_file(args.ref)
    verite = ref[ref["produit"] == args.produit]
    if verite.empty:
        raise SystemExit(
            f"Produit inconnu : {args.produit}\nDisponibles :\n  "
            + "\n  ".join(sorted(ref["produit"]))
        )
    verite = verite.iloc[0]

    aois = gpd.read_file(args.aoi)
    emprise = aois[aois["produit"] == args.produit]
    if emprise.empty:  # repli : même activation/AOI, autre produit
        emprise = aois[(aois["activation"] == verite["activation"])
                       & (aois["aoi"] == verite["aoi"])]
    if emprise.empty:
        raise SystemExit(f"Aucune emprise EMS trouvée pour {args.produit}")

    client = Client.open(STAC)
    it_pre, it_post = item(client, args.pre), item(client, args.post)

    # Les deux images doivent être sur la même tuile MGRS : mêmes grille et CRS,
    # donc aucune reprojection (et aucun rééchantillonnage) avant la différence.
    tuile_pre = it_pre.properties.get("grid:code") or it_pre.id.split("_")[1]
    tuile_post = it_post.properties.get("grid:code") or it_post.id.split("_")[1]
    if tuile_pre != tuile_post:
        raise SystemExit(
            f"Tuiles différentes ({tuile_pre} / {tuile_post}) : le dNBR exigerait un "
            "rééchantillonnage, non implémenté volontairement."
        )

    crs_tuile = rasterio.crs.CRS.from_epsg(
        int(it_post.properties["proj:epsg"]) if "proj:epsg" in it_post.properties
        else it_post.properties["proj:code"].split(":")[-1]
    )
    bounds = emprise.to_crs(crs_tuile).buffer(args.marge_m).total_bounds

    print(f"Cas          {verite['feu']} — vérité {args.produit}")
    print(f"  EMS        {verite['date_situation']}  {verite['capteur_post']}  "
          f"{verite['surface_ha']} ha  (MMU {verite['mmu']}, {verite['echelle_analyse']})")
    if verite["reserves"]:
        print(f"  réserves   {verite['reserves']}")
    print(f"  avant      {it_pre.id}  {it_pre.datetime:%Y-%m-%d}  "
          f"nuages {it_pre.properties['eo:cloud_cover']:.1f} %")
    print(f"  après      {it_post.id}  {it_post.datetime:%Y-%m-%d}  "
          f"nuages {it_post.properties['eo:cloud_cover']:.1f} %")

    nbr_pre, val_pre, transform, _ = nbr(it_pre, bounds)
    nbr_post, val_post, _, _ = nbr(it_post, bounds)
    valide = val_pre & val_post
    dnbr = nbr_pre - nbr_post

    # L'emprise EMS peut déborder de la tuile Sentinel-2 (une AOI n'est pas alignée
    # sur la grille MGRS). On restreint donc la comparaison à l'intersection
    # emprise EMS ∩ image : sinon la vérité située hors image compterait comme
    # omission, ce qui dégraderait le rappel sans raison.
    h, w = dnbr.shape
    footprint = box(*rasterio.transform.array_bounds(h, w, transform))
    emprise_tuile = emprise.to_crs(crs_tuile).union_all().intersection(footprint)
    if emprise_tuile.is_empty:
        raise SystemExit("L'emprise EMS ne recouvre pas l'image : mauvaise tuile ?")

    # Mesuré avant toute exclusion : c'est bien la part de l'AOI que l'image couvre.
    part_couverte = (
        gpd.GeoSeries([emprise_tuile], crs=crs_tuile).to_crs(CRS_METRIQUE).area.iloc[0]
        / emprise.to_crs(CRS_METRIQUE).union_all().area
    )

    # Une cicatrice de feu récente reste sombre : un sur-brûlage n'y produit presque
    # aucun écart de NBR (mesuré à Landiras, médiane dNBR -0,07 sur la zone rebrûlée
    # en août dans le périmètre de juillet). L'exclure permet de mesurer la méthode
    # là où elle peut fonctionner, sans masquer la limite — les deux chiffres se
    # rapportent.
    for produit_exclu in args.exclure_produit:
        ligne = ref[ref["produit"] == produit_exclu]
        if ligne.empty:
            raise SystemExit(f"Produit à exclure inconnu : {produit_exclu}")
        emprise_tuile = emprise_tuile.difference(
            ligne.to_crs(crs_tuile).union_all()
        )

    emprise_m = (
        gpd.GeoSeries([emprise_tuile], crs=crs_tuile).to_crs(CRS_METRIQUE).iloc[0]
    )

    dans_aoi = rasterio.features.geometry_mask(
        [emprise_tuile], out_shape=dnbr.shape, transform=transform, invert=True,
    )
    exploitable = valide & dans_aoi
    couverture = exploitable.sum() / max(dans_aoi.sum(), 1)
    print(f"  emprise    {part_couverte:.1%} de l'AOI EMS couverte par la tuile "
          f"{tuile_post}")
    print(f"  pixels     {dans_aoi.sum()} comparables, "
          f"{couverture:.1%} exploitables (nuages/ombres écartés)")

    verite_m = gpd.GeoSeries([verite["geometry"]], crs=ref.crs).to_crs(CRS_METRIQUE)
    verite_m = verite_m.union_all().intersection(emprise_m)
    aire_verite = verite_m.area / 1e4

    # Part de la vérité couverte par des pixels exploitables : c'est le plafond
    # atteignable du rappel. Sans ce chiffre, une omission due aux nuages se lit
    # comme un échec de la méthode.
    dans_verite = rasterio.features.geometry_mask(
        gpd.GeoSeries([verite_m], crs=CRS_METRIQUE).to_crs(crs_tuile),
        out_shape=dnbr.shape, transform=transform, invert=True,
    )
    plafond_rappel = (
        (exploitable & dans_verite).sum() / dans_verite.sum() if dans_verite.any() else 0.0
    )
    print(f"  vérité     {aire_verite:.1f} ha comparables, "
          f"{plafond_rappel:.1%} sur pixels exploitables (plafond du rappel)")

    suffixe = "__hors-cicatrice" if args.exclure_produit else ""
    dossier = args.out / f"{args.produit}__{args.pre}__{args.post}{suffixe}"
    dossier.mkdir(parents=True, exist_ok=True)

    resultats = []
    for seuil in SEUILS:
        masque = exploitable & (dnbr >= seuil)
        polys = polygoniser(masque, transform, crs_tuile, args.surface_min_ha)
        if polys.empty:
            detecte = None
            aire_det = inter = union = 0.0
        else:
            detecte = polys.union_all().intersection(emprise_m)
            aire_det = detecte.area / 1e4
            inter = detecte.intersection(verite_m).area / 1e4
            union = detecte.union(verite_m).area / 1e4

        resultats.append({
            "seuil": seuil,
            "n_polygones": len(polys),
            "surface_detectee_ha": round(aire_det, 1),
            "iou": round(inter / union, 3) if union else 0.0,
            # rappel = part de la vérité retrouvée ; précision = part du détecté qui est juste
            "rappel": round(inter / aire_verite, 3) if aire_verite else 0.0,
            "precision": round(inter / aire_det, 3) if aire_det else 0.0,
            "ecart_surface_pct": round(100 * (aire_det - aire_verite) / aire_verite, 1)
            if aire_verite else None,
        })
        if detecte is not None and abs(seuil - args.seuil_retenu) < 1e-9:
            polys["seuil"] = seuil
            polys["surface_ha"] = (polys.area / 1e4).round(2)
            polys.to_file(dossier / "polygones.geojson", driver="GeoJSON")

    meilleur = max(resultats, key=lambda r: r["iou"])

    print()
    print(f"  {'seuil':>6} {'n':>4} {'surface ha':>11} {'IoU':>6} {'rappel':>7} "
          f"{'précis.':>8} {'écart %':>8}")
    for r in resultats:
        marque = " <-" if r is meilleur else ""
        print(f"  {r['seuil']:>6.2f} {r['n_polygones']:>4} {r['surface_detectee_ha']:>11.1f} "
              f"{r['iou']:>6.3f} {r['rappel']:>7.3f} {r['precision']:>8.3f} "
              f"{r['ecart_surface_pct']:>8.1f}{marque}")

    metriques = {
        "produit_verite": args.produit,
        "feu": verite["feu"],
        "date_situation_ems": verite["date_situation"],
        "capteur_ems": verite["capteur_post"],
        "reserves_ems": verite["reserves"],
        "image_avant": {"id": it_pre.id, "date": f"{it_pre.datetime:%Y-%m-%d}",
                        "nuages_pct": it_pre.properties["eo:cloud_cover"]},
        "image_apres": {"id": it_post.id, "date": f"{it_post.datetime:%Y-%m-%d}",
                        "nuages_pct": it_post.properties["eo:cloud_cover"]},
        "surface_min_ha": args.surface_min_ha,
        "seuil_retenu": args.seuil_retenu,
        "produits_exclus": args.exclure_produit,
        "surface_verite_ha": round(aire_verite, 1),
        "part_aoi_couverte_par_tuile": round(float(part_couverte), 4),
        "plafond_rappel": round(float(plafond_rappel), 4),
        "couverture_exploitable": round(float(couverture), 4),
        "resultats": resultats,
        "meilleur_seuil": meilleur,
    }
    (dossier / "metriques.json").write_text(
        json.dumps(metriques, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with rasterio.open(
        dossier / "dnbr.tif", "w", driver="GTiff", height=dnbr.shape[0],
        width=dnbr.shape[1], count=1, dtype="float32", crs=crs_tuile,
        transform=transform, nodata=np.nan, compress="deflate",
    ) as ds:
        ds.write(np.where(exploitable, dnbr, np.nan).astype("float32"), 1)

    print(f"\n  -> {dossier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
