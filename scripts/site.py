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
    info_path, geo_path = dossier / "info.json", dossier / "perimetre.geojson"
    if not (info_path.exists() and geo_path.exists()):
        return None
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["_polys"] = gpd.read_file(geo_path).to_crs(CRS_METRIQUE)
    return info


def fusionner(feux: list[dict]) -> list[dict]:
    """Fusionne les détections qui décrivent le même incendie."""
    feux = sorted(feux, key=lambda f: -f["_polys"].area.sum())
    retenus: list[dict] = []

    for f in feux:
        geom = f["_polys"].union_all()
        for garde in retenus:
            g = garde["_polys"].union_all()
            inter = geom.intersection(g).area
            if inter > RECOUVREMENT_DOUBLON * min(geom.area, g.area):
                # Le plus grand est conservé ; on lui ajoute la géométrie du doublon et
                # on cumule les points chauds, qui restent des observations valides.
                garde["_polys"] = gpd.GeoDataFrame(
                    {"geometry": [g.union(geom)]}, geometry="geometry", crs=CRS_METRIQUE)
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
    feu, isoles = separer(info["_polys"])
    surface = float(feu.area.sum() / 1e4)

    reserves = []
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
        "commune": info.get("commune", ""),
        "departement": info.get("departement", ""),
        "surface_ha": round(surface, 1),
        "debut": info["premier_point_chaud"],
        "fin": info["dernier_point_chaud"],
        "n_points_chauds": info["n_points_chauds"],
        "image_avant": info["image_avant"]["date"],
        "image_apres": info["image_apres"]["date"],
        "latence_jours": info.get("latence_jours"),
        "seuil_dnbr": info.get("seuil_dnbr"),
        "part_masquee": info.get("part_masquee"),
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


GABARIT = """<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brûlis — zones brûlées de France en open data</title>
<meta name="description" content="Périmètres et surfaces des feux de forêt en France,
calculés depuis Sentinel-2 et les points chauds VIIRS. Open data.">
<link href="vendor/maplibre-gl.css" rel="stylesheet">
<style>
  :root { --fond:#12141a; --panneau:#1a1d24; --bord:#2c313b; --texte:#e9ecf1;
          --doux:#9aa3b0; --feu:#ff6b35; --isole:#ffd166; --chaud:#ff2d55; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
              background:var(--fond); color:var(--texte); }
  #carte { position:absolute; inset:0 0 0 380px; }
  .panneau { position:absolute; top:0; left:0; bottom:0; width:380px; z-index:2;
             background:var(--panneau); border-right:1px solid var(--bord);
             overflow-y:auto; padding:18px 20px 28px; }
  h1 { font-size:21px; margin:0 0 2px; letter-spacing:-.3px; }
  .accroche { color:var(--doux); font-size:13.5px; margin:0 0 14px; }
  .avert { background:#3a2412; border:1px solid #6b3f1d; color:#ffbe8a;
           padding:9px 11px; border-radius:8px; font-size:12.5px; margin-bottom:16px; }
  .total { display:flex; gap:14px; margin-bottom:16px; }
  .total div { flex:1; background:#20242c; border:1px solid var(--bord);
               border-radius:9px; padding:9px 11px; }
  .total b { display:block; font-size:19px; color:var(--feu);
             font-variant-numeric:tabular-nums; }
  .total span { font-size:11.5px; color:var(--doux); text-transform:uppercase;
                letter-spacing:.5px; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.9px; color:var(--doux);
       margin:20px 0 8px; font-weight:600; }
  .feu { border:1px solid var(--bord); border-radius:9px; padding:10px 12px;
         margin-bottom:8px; cursor:pointer; background:#20242c; }
  .feu:hover { border-color:#4a515e; }
  .feu.actif { border-color:var(--feu); background:#2a2119; }
  .feu .nom { font-weight:600; }
  .feu .ha { float:right; color:var(--feu); font-variant-numeric:tabular-nums; }
  .feu .meta { color:var(--doux); font-size:12.5px; margin-top:2px; }
  .detail { font-size:13px; background:#191c22; border:1px solid var(--bord);
            border-radius:9px; padding:11px 12px; margin-top:10px; }
  .detail dl { display:grid; grid-template-columns:auto 1fr; gap:3px 12px; margin:0; }
  .detail dt { color:var(--doux); } .detail dd { margin:0; font-variant-numeric:tabular-nums; }
  .reserve { color:#ffbe8a; font-size:12.5px; margin-top:9px; }
  .legende div { display:flex; align-items:center; gap:8px; font-size:13px; margin:5px 0; }
  .pastille { width:13px; height:13px; border-radius:3px; border:2px solid; }
  .pastille.rond { border-radius:50%; }
  a { color:#7cc0ff; }
  .liens { margin-top:16px; font-size:13px; display:flex; gap:14px; flex-wrap:wrap; }
  details { margin-top:16px; font-size:13px; color:var(--doux); }
  summary { cursor:pointer; color:var(--texte); font-weight:600; }
  details p, details li { line-height:1.55; }
  .bascule { position:absolute; top:12px; right:12px; z-index:3; display:flex; gap:6px; }
  .bascule button { background:#1a1d24dd; color:var(--texte); border:1px solid var(--bord);
                    padding:7px 12px; border-radius:7px; cursor:pointer; font-size:13px; }
  .bascule button.actif { background:var(--feu); border-color:var(--feu); color:#1a1005; }
  .pied { color:var(--doux); font-size:12px; margin-top:16px; }
  @media (max-width:760px){ .panneau{ position:relative; width:auto; height:auto;
    max-height:56%; border-right:0; border-bottom:1px solid var(--bord);} #carte{ inset:56% 0 0 0; } }
</style></head><body>
<div class="panneau">
  <h1>Brûlis</h1>
  <p class="accroche">Périmètres et surfaces brûlées de France, calculés depuis
  Sentinel-2. Open data, code ouvert.</p>
  <div class="avert"><b>Ce n'est pas un outil opérationnel.</b> Les images arrivent avec
  1 à 3 jours de retard. Pour une urgence, appelez le 18 ou le 112.</div>

  <div class="total">
    <div><b id="tHa">—</b><span>hectares</span></div>
    <div><b id="tFeux">—</b><span>feux</span></div>
    <div><b id="tLat">—</b><span>latence méd.</span></div>
  </div>

  <h2>Feux détectés</h2>
  <div id="liste"></div>
  <div id="detail"></div>

  <h2>Légende</h2>
  <div class="legende">
    <div><span class="pastille" style="border-color:var(--feu);background:#ff6b3544"></span>
      périmètre brûlé calculé</div>
    <div><span class="pastille" style="border-color:var(--isole);background:#ffd16633"></span>
      détection isolée — souvent une coupe forestière</div>
    <div><span class="pastille rond" style="border-color:var(--chaud);background:#ff2d5566"></span>
      point chaud VIIRS (~3 h de latence)</div>
  </div>

  <details><summary>Méthode et limites</summary>
    <p>Les <b>points chauds</b> viennent de NASA FIRMS (VIIRS, pixels de 375 m) : environ
    3 h après le passage du satellite. Ils disent « ça chauffe ici », pas « voilà le
    contour ».</p>
    <p>Le <b>périmètre</b> est calculé sur deux images Sentinel-2, avant et après le feu :
    un indice de brûlure (NBR) est construit à partir du proche et du moyen infrarouge
    (bandes B8A et B12, 20 m), et leur différence (dNBR) fait ressortir la végétation
    détruite. Au-delà d'un seuil, les pixels sont regroupés en polygones.</p>
    <p><b>Fiabilité mesurée.</b> La méthode a été confrontée aux périmètres officiels
    Copernicus EMS sur les grands feux de Gironde de 2022 — tracés à la main sur des
    images à 0,5–1,5 m : recouvrement de 0,75 à 0,92, avec un réglage unique. Détail et
    chiffres dans le dépôt.</p>
    <p><b>Ce que la méthode rate, et c'est mesuré :</b></p>
    <ul>
      <li>les <b>nuages et la fumée</b> masquent des zones entières ; la part masquée est
      indiquée pour chaque feu ;</li>
      <li>un feu qui <b>rebrûle une zone brûlée récemment est invisible</b> : le sol est
      déjà sombre, il n'y a plus de contraste ;</li>
      <li>les <b>coupes forestières</b> ressemblent à des brûlis — d'où la couche
      « détections isolées », publiée séparément ;</li>
      <li>un feu <b>encore actif</b> donne un périmètre partiel, daté : il grandira au
      prochain passage satellite.</li>
    </ul>
    <p><b>Latence.</b> Un satellite optique ne voit la zone qu'à son prochain passage sans
    nuages : 1 à 3 jours sur la France. Ce n'est pas un choix, c'est la physique.</p>
  </details>

  <div class="liens">
    <a href="data/feux.geojson" download>GeoJSON</a>
    <a href="data/feux.csv" download>CSV</a>
    <a href="data/isolees.geojson" download>Détections isolées</a>
    <a href="data/points_chauds.geojson" download>Points chauds</a>
  </div>
  <p class="pied">Mise à jour : __MAJ__ · Fond : IGN Géoplateforme · Images : Copernicus
  Sentinel-2 · Points chauds : NASA FIRMS. Données produites sous Licence Ouverte 2.0.</p>
</div>

<div class="bascule">
  <button id="bPlan" class="actif">Plan</button>
  <button id="bOrtho">Photo</button>
  <button id="bChauds" class="actif">Points chauds</button>
</div>
<div id="carte"></div>

<script src="vendor/maplibre-gl.js"></script>
<script>
const FEUX = __FEUX__, ISOLEES = __ISOLEES__, CHAUDS = __CHAUDS__;
const ign = (couche, fmt) => "https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0" +
  "&REQUEST=GetTile&LAYER=" + couche + "&STYLE=normal&TILEMATRIXSET=PM" +
  "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=" + fmt;

const carte = new maplibregl.Map({
  container:"carte", hash:true,
  style:{ version:8, sources:{
      plan:{type:"raster", tiles:[ign("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2","image/png")],
            tileSize:256, attribution:"IGN Géoplateforme"},
      ortho:{type:"raster", tiles:[ign("ORTHOIMAGERY.ORTHOPHOTOS","image/jpeg")],
             tileSize:256, attribution:"IGN Géoplateforme"}},
    layers:[{id:"plan", type:"raster", source:"plan"},
            {id:"ortho", type:"raster", source:"ortho", layout:{visibility:"none"}}]},
  center:[2.4,46.6], zoom:5
});
carte.addControl(new maplibregl.NavigationControl(), "bottom-right");
carte.addControl(new maplibregl.ScaleControl(), "bottom-left");

carte.on("load", () => {
  carte.addSource("chauds", {type:"geojson", data:CHAUDS});
  carte.addLayer({id:"chauds-pt", type:"circle", source:"chauds",
    paint:{"circle-radius":["interpolate",["linear"],["zoom"],5,2,10,4.5,13,7],
           "circle-color":"#ff2d55","circle-opacity":0.55,
           "circle-stroke-width":0.6,"circle-stroke-color":"#ff2d55"}});

  carte.addSource("isolees", {type:"geojson", data:ISOLEES});
  carte.addLayer({id:"isolees-fond", type:"fill", source:"isolees",
    paint:{"fill-color":"#ffd166","fill-opacity":0.22}});
  carte.addLayer({id:"isolees-trait", type:"line", source:"isolees",
    paint:{"line-color":"#ffd166","line-width":1}});

  carte.addSource("feux", {type:"geojson", data:FEUX});
  carte.addLayer({id:"feux-fond", type:"fill", source:"feux",
    paint:{"fill-color":"#ff6b35","fill-opacity":0.34}});
  carte.addLayer({id:"feux-trait", type:"line", source:"feux",
    paint:{"line-color":"#ff6b35","line-width":2}});

  if (FEUX.features.length) cadrer(FEUX);
  for (const c of ["feux-fond","isolees-fond"]) {
    carte.on("mouseenter", c, () => carte.getCanvas().style.cursor = "pointer");
    carte.on("mouseleave", c, () => carte.getCanvas().style.cursor = "");
  }
  carte.on("click", "feux-fond", e => choisir(
    FEUX.features.findIndex(f => f.properties.id === e.features[0].properties.id)));
  carte.on("click", "isolees-fond", e => new maplibregl.Popup().setLngLat(e.lngLat)
    .setHTML(`<b>Détection isolée</b><br>${e.features[0].properties.surface_ha} ha<br>
      <span style="color:#8a6d3b">souvent une coupe forestière</span>`).addTo(carte));
});

function bornes(fc){
  const b = new maplibregl.LngLatBounds();
  const parcourir = c => typeof c[0] === "number" ? b.extend(c) : c.forEach(parcourir);
  fc.features.forEach(f => parcourir(f.geometry.coordinates));
  return b;
}
function cadrer(fc){
  if (!fc.features.length) return;
  carte.fitBounds(bornes(fc), {padding:60, maxZoom:13});
}

const f = FEUX.features;
const total = f.reduce((s,x) => s + x.properties.surface_ha, 0);
const lat = f.map(x => x.properties.latence_jours).filter(v => v != null).sort((a,b) => a-b);
document.getElementById("tHa").textContent = Math.round(total).toLocaleString("fr-FR");
document.getElementById("tFeux").textContent = f.length;
document.getElementById("tLat").textContent =
  lat.length ? lat[Math.floor(lat.length/2)] + " j" : "—";

const liste = document.getElementById("liste"), detail = document.getElementById("detail");
liste.innerHTML = f.map((x,i) => {
  const p = x.properties;
  return `<div class="feu" data-i="${i}"><span class="ha">${p.surface_ha.toLocaleString("fr-FR")} ha</span>
    <div class="nom">${p.feu}</div>
    <div class="meta">départ ${p.debut} · image ${p.image_apres}</div></div>`;
}).join("") || '<p style="color:var(--doux)">Aucun feu détecté sur la période.</p>';

function choisir(i){
  if (i < 0) return;
  document.querySelectorAll(".feu").forEach((e,j) => e.classList.toggle("actif", j === i));
  const p = f[i].properties;
  detail.innerHTML = `<div class="detail"><dl>
    <dt>Surface</dt><dd><b>${p.surface_ha.toLocaleString("fr-FR")} ha</b></dd>
    <dt>Points chauds</dt><dd>${p.n_points_chauds} (${p.debut} → ${p.fin})</dd>
    <dt>Image avant</dt><dd>${p.image_avant}</dd>
    <dt>Image après</dt><dd>${p.image_apres}</dd>
    <dt>Latence</dt><dd>${p.latence_jours} jour(s)</dd>
    <dt>Zone masquée</dt><dd>${(p.part_masquee*100).toFixed(0)} %</dd>
  </dl>` + (p.reserves.length
    ? `<div class="reserve">⚠ ${p.reserves.join("<br>⚠ ")}</div>` : "") + "</div>";
  cadrer({features:[f[i]]});
}
liste.onclick = e => { const c = e.target.closest(".feu"); if (c) choisir(+c.dataset.i); };

function basculerFond(quel){
  for (const c of ["plan","ortho"])
    carte.setLayoutProperty(c, "visibility", c === quel ? "visible" : "none");
  document.getElementById("bPlan").classList.toggle("actif", quel === "plan");
  document.getElementById("bOrtho").classList.toggle("actif", quel === "ortho");
}
document.getElementById("bPlan").onclick = () => basculerFond("plan");
document.getElementById("bOrtho").onclick = () => basculerFond("ortho");
document.getElementById("bChauds").onclick = function(){
  this.classList.toggle("actif");
  carte.setLayoutProperty("chauds-pt", "visibility",
    this.classList.contains("actif") ? "visible" : "none");
};
</script></body></html>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feux", type=Path, default=Path("data/feux"))
    p.add_argument("--points-chauds", type=Path,
                   default=Path("data/work/foyers_points.geojson"))
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

    feux.sort(key=lambda f: -f["properties"]["surface_ha"])

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

    colonnes = ["id", "feu", "commune", "departement", "surface_ha", "debut", "fin",
                "n_points_chauds", "image_avant", "image_apres", "latence_jours",
                "seuil_dnbr", "part_masquee", "calcule_le"]
    with (donnees / "feux.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colonnes, extrasaction="ignore")
        w.writeheader()
        w.writerows(f["properties"] for f in feux)

    maj = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    html = (GABARIT
            .replace("__FEUX__", json.dumps(fc_feux, ensure_ascii=False))
            .replace("__ISOLEES__", json.dumps(fc_isol, ensure_ascii=False))
            .replace("__CHAUDS__", json.dumps(chauds, ensure_ascii=False))
            .replace("__MAJ__", maj))
    (args.out / "index.html").write_text(html, encoding="utf-8")

    total = sum(f["properties"]["surface_ha"] for f in feux)
    print()
    for f in feux:
        pr = f["properties"]
        print(f"  {pr['feu'][:34]:36s} {pr['surface_ha']:>9.1f} ha  "
              f"image {pr['image_apres']}  latence {pr['latence_jours']} j")
    poids = sum(x.stat().st_size for x in args.out.rglob("*") if x.is_file()) / 1e6
    print(f"\n  {len(feux)} feux, {total:.0f} ha, {len(isolees)} détections isolées, "
          f"{len(chauds['features'])} points chauds")
    print(f"  → {args.out}/  ({poids:.1f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
