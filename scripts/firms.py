"""Points chauds VIIRS/MODIS (NASA FIRMS) : récupération et regroupement en foyers.

C'est la brique « temps réel » : FIRMS publie les détections thermiques environ 3 h après
le passage du satellite, alors qu'un périmètre Sentinel-2 demande 1 à 3 jours. Les points
chauds servent donc à deux choses :

  1. dire « ça brûle ici, maintenant » sur la carte ;
  2. **amorcer** la détection — inutile de balayer la France entière en dNBR, on ne calcule
     que là où quelque chose a chauffé.

Un point chaud n'est pas un feu : c'est un pixel de 375 m (VIIRS) anormalement chaud. Une
torchère industrielle, un brûlage agricole ou un incendie de bâtiment en déclenchent aussi.
Le regroupement spatial puis le dNBR font le tri.

Clé gratuite requise : https://firms.modaps.eosdis.nasa.gov/api/map_key/ → `.env`.
"""

import csv
import io
import os
import sys
import urllib.request
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

BASE = "https://firms.modaps.eosdis.nasa.gov"
FRANCE = (-5.3, 41.2, 9.7, 51.2)  # ouest, sud, est, nord
CRS_METRIQUE = 2154

# Capteurs temps quasi réel. On interroge les trois VIIRS : chacun passe à une heure
# différente, ce qui densifie la couverture horaire.
CAPTEURS = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")

# Distance de regroupement : deux points chauds plus proches que ça appartiennent au même
# foyer. Un pixel VIIRS fait 375 m ; 2 km tolère les trous sans fusionner deux incendies
# distincts.
DISTANCE_FOYER = 2000


def charger_env(chemin: Path = Path(".env")) -> None:
    """Charge un .env minimal dans l'environnement (pas de dépendance externe)."""
    if not chemin.exists():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        os.environ.setdefault(cle.strip(), valeur.strip())


def cle() -> str:
    charger_env()
    k = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not k:
        raise SystemExit(
            "FIRMS_MAP_KEY absente. Copier .env.example en .env et y mettre la clé "
            "(gratuite : https://firms.modaps.eosdis.nasa.gov/api/map_key/)."
        )
    return k


def _get(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "brulis/0.1 (open data)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def quota() -> dict:
    """État du quota de la clé — utile pour diagnostiquer un échec silencieux."""
    txt = _get(f"{BASE}/mapserver/mapkey_status/?MAP_KEY={cle()}")
    try:
        import json
        return json.loads(txt)
    except Exception:
        return {"brut": txt[:200]}


def points_chauds(bbox=FRANCE, jours: int = 3, capteurs=CAPTEURS) -> gpd.GeoDataFrame:
    """Points chauds sur les `jours` derniers jours. Un capteur muet n'arrête pas le reste."""
    zone = ",".join(str(round(v, 4)) for v in bbox)
    lignes, echecs = [], []

    for capteur in capteurs:
        # Les virgules de la zone doivent rester littérales : encodées en %2C,
        # l'API répond 400. Ne pas « corriger » en ajoutant un quote().
        url = f"{BASE}/api/area/csv/{cle()}/{capteur}/{zone}/{jours}"
        try:
            texte = _get(url)
        except Exception as exc:
            echecs.append(f"{capteur}: {exc}")
            continue
        if not texte.lstrip().lower().startswith("country_id") and "latitude" not in texte[:400]:
            echecs.append(f"{capteur}: réponse inattendue « {texte.strip()[:120]} »")
            continue
        for r in csv.DictReader(io.StringIO(texte)):
            r["capteur"] = capteur
            lignes.append(r)

    for e in echecs:
        print(f"  FIRMS {e}")

    if not lignes:
        return gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs=4326
        ).assign(acq_date=pd.Series(dtype="object"))

    df = pd.DataFrame(lignes)
    for col in ("latitude", "longitude", "bright_ti4", "frp", "confidence"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    gdf = gpd.GeoDataFrame(
        df, geometry=[Point(x, y) for x, y in zip(df["longitude"], df["latitude"])],
        crs=4326,
    )
    # `n` = nominal, `h` = high. On écarte `l` (low) : trop de faux positifs industriels.
    if "confidence" in gdf and gdf["confidence"].dtype == object:
        gdf = gdf[gdf["confidence"].astype(str).str.lower() != "l"]
    return gdf.reset_index(drop=True)


def foyers(gdf: gpd.GeoDataFrame, distance: float = DISTANCE_FOYER) -> gpd.GeoDataFrame:
    """Regroupe les points chauds en foyers (un foyer ≈ un incendie candidat)."""
    if gdf.empty:
        return gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs=CRS_METRIQUE
        )

    m = gdf.to_crs(CRS_METRIQUE)
    # Tampon puis fusion : deux points à moins de `distance` se touchent, donc se fondent
    # dans le même agrégat. Plus simple qu'un DBSCAN et sans dépendance supplémentaire.
    agregats = m.buffer(distance / 2).union_all()
    parts = list(getattr(agregats, "geoms", [agregats]))

    zones = gpd.GeoDataFrame({"geometry": parts}, geometry="geometry", crs=CRS_METRIQUE)
    jointure = gpd.sjoin(m, zones.reset_index(names="foyer"), predicate="within")

    lignes = []
    for identifiant, groupe in jointure.groupby("foyer"):
        dates = sorted(str(d) for d in groupe.get("acq_date", pd.Series(dtype=str)).dropna())
        lignes.append({
            "foyer": int(identifiant),
            "n_points": len(groupe),
            "date_debut": dates[0] if dates else "",
            "date_fin": dates[-1] if dates else "",
            "frp_max": float(groupe["frp"].max()) if "frp" in groupe else None,
            "frp_total": float(groupe["frp"].sum()) if "frp" in groupe else None,
            "geometry": zones.geometry.iloc[int(identifiant)],
        })

    out = gpd.GeoDataFrame(lignes, geometry="geometry", crs=CRS_METRIQUE)
    # Le FRP cumulé est le meilleur proxy disponible de l'ampleur : on traite d'abord
    # les foyers les plus intenses.
    return out.sort_values("frp_total", ascending=False).reset_index(drop=True)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jours", type=int, default=3, help="fenêtre FIRMS (1-10)")
    p.add_argument("--min-points", type=int, default=2,
                   help="foyers d'au moins N points chauds (défaut 2 : écarte les isolés)")
    p.add_argument("--out", type=Path, default=Path("data/work/foyers.geojson"))
    args = p.parse_args()

    q = quota()
    print(f"  quota FIRMS : {q.get('current_transactions', '?')}/"
          f"{q.get('transaction_limit', '?')} sur {q.get('transaction_interval', '?')}")

    gdf = points_chauds(jours=args.jours)
    print(f"  {len(gdf)} points chauds sur {args.jours} j en France")
    if gdf.empty:
        return 0

    f = foyers(gdf)
    retenus = f[f["n_points"] >= args.min_points]
    print(f"  {len(f)} foyers, dont {len(retenus)} d'au moins {args.min_points} points\n")

    apercu = retenus.to_crs(4326)
    for _, r in apercu.head(15).iterrows():
        c = r.geometry.centroid
        print(f"  foyer {r['foyer']:>4}  {r['n_points']:>4} pts  "
              f"FRP {r['frp_total'] or 0:>8.1f}  {r['date_debut']}→{r['date_fin']}  "
              f"({c.y:.3f}, {c.x:.3f})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    retenus.to_crs(4326).to_file(args.out, driver="GeoJSON")
    print(f"\n  → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
