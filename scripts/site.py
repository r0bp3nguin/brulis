"""Construit le site public statique : carte, données ouvertes, page méthode.

Sortie : `site/` — hébergeable tel quel (GitHub Pages, stockage objet, n'importe quoi).
Aucun serveur, aucune base, aucun compte.

Séparation des détections, sans vérité terrain (le site tourne aussi sur des feux
inconnus) : le **périmètre** est le plus grand polygone plus tout ce qui l'entoure à moins
de `DISTANCE_REGROUPEMENT`. Le reste est publié à part, en « détections isolées » — ce sont
majoritairement des coupes forestières. On les montre au lieu de les jeter en silence, et
l'étape 2 les tranchera avec les points chauds VIIRS.

Usage :
    python scripts/site.py
    open site/index.html
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import env_geo  # noqa: F401
import geopandas as gpd

CRS_METRIQUE = 2154
DISTANCE_REGROUPEMENT = 2000  # m — au-delà, on ne parle plus du même incendie
TOLERANCE_WEB = 10  # m — simplification des contours pour l'affichage


def separer(polys: gpd.GeoDataFrame):
    """(périmètre du feu, détections isolées) à partir des polygones bruts."""
    if polys.empty:
        return polys, polys
    # argmax positionnel : idxmax renverrait une étiquette d'index, qui ne coïncide avec
    # une position que si l'index est un RangeIndex — pas garanti après un filtrage.
    principal = polys.geometry.iloc[polys.area.values.argmax()]
    proche = polys.geometry.distance(principal) <= DISTANCE_REGROUPEMENT
    return polys[proche], polys[~proche]


def construire(dossier: Path, ref: gpd.GeoDataFrame) -> tuple[dict, list] | None:
    chemin = dossier / "polygones.geojson"
    if not chemin.exists():
        return None
    m = json.loads((dossier / "metriques.json").read_text(encoding="utf-8"))

    polys = gpd.read_file(chemin).to_crs(CRS_METRIQUE)
    feu, isoles = separer(polys)

    seuil = m.get("seuil_retenu")
    ligne = next((r for r in m["resultats"] if abs(r["seuil"] - seuil) < 1e-9),
                 m["meilleur_seuil"])

    masque = 1 - (m.get("plafond_rappel") or 1)
    reserves = []
    if masque > 0.05:
        reserves.append(f"{masque:.0%} de la zone masquée par les nuages ou la fumée")
    if m.get("produits_exclus"):
        reserves.append("zone rebrûlée sur une cicatrice récente : non détectable")
    if len(isoles):
        reserves.append(f"{len(isoles)} détections isolées écartées du périmètre")

    props = {
        "feu": m["feu"],
        "surface_ha": round(float(feu.area.sum() / 1e4), 1),
        "image_avant": m["image_avant"]["date"],
        "image_apres": m["image_apres"]["date"],
        "nuages_apres_pct": m["image_apres"]["nuages_pct"],
        "seuil_dnbr": seuil,
        "part_masquee": round(masque, 3),
        "reserves": reserves,
        # Comparaison à la référence officielle : c'est ce qui rend le chiffre vérifiable.
        "ems_produit": m["produit_verite"],
        "ems_date": m["date_situation_ems"],
        "ems_surface_ha": m["surface_verite_ha"],
        "recouvrement_iou": ligne["iou"],
        "ecart_surface_pct": ligne["ecart_surface_pct"],
    }

    geom = feu.dissolve().simplify(TOLERANCE_WEB).to_crs(4326).iloc[0]
    trait = {"type": "Feature", "properties": props,
             "geometry": json.loads(gpd.GeoSeries([geom], crs=4326).to_json())
             ["features"][0]["geometry"]}

    isoles_feats = []
    if len(isoles):
        gi = isoles.copy()
        gi["surface_ha"] = (gi.area / 1e4).round(2)
        gi["feu"] = m["feu"]
        gi = gi.set_geometry(gi.simplify(TOLERANCE_WEB)).to_crs(4326)
        isoles_feats = json.loads(gi[["feu", "surface_ha", "geometry"]].to_json())["features"]

    return trait, isoles_feats


GABARIT = """<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brûlis — zones brûlées de France en open data</title>
<meta name="description" content="Périmètres et surfaces des feux de forêt en France,
calculés depuis Sentinel-2, en open data.">
<link href="vendor/maplibre-gl.css" rel="stylesheet">
<style>
  :root { --fond:#12141a; --panneau:#1a1d24; --bord:#2c313b; --texte:#e9ecf1;
          --doux:#9aa3b0; --feu:#ff6b35; --isole:#ffd166; --ems:#4da3ff; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
              background:var(--fond); color:var(--texte); }
  #carte { position:absolute; inset:0; }
  .panneau { position:absolute; top:0; left:0; bottom:0; width:370px; z-index:2;
             background:var(--panneau); border-right:1px solid var(--bord);
             overflow-y:auto; padding:18px 20px 28px; }
  h1 { font-size:20px; margin:0 0 2px; letter-spacing:-.2px; }
  .accroche { color:var(--doux); font-size:13.5px; margin:0 0 16px; }
  .avert { background:#3a2412; border:1px solid #6b3f1d; color:#ffbe8a;
           padding:9px 11px; border-radius:8px; font-size:12.5px; margin-bottom:16px; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.9px; color:var(--doux);
       margin:20px 0 8px; font-weight:600; }
  .feu { border:1px solid var(--bord); border-radius:9px; padding:11px 12px;
         margin-bottom:9px; cursor:pointer; background:#20242c; }
  .feu:hover { border-color:#4a515e; }
  .feu.actif { border-color:var(--feu); background:#2a2119; }
  .feu .nom { font-weight:600; }
  .feu .ha { float:right; color:var(--feu); font-variant-numeric:tabular-nums; }
  .feu .meta { color:var(--doux); font-size:12.5px; margin-top:3px; }
  .detail { font-size:13px; }
  .detail dl { display:grid; grid-template-columns:auto 1fr; gap:3px 12px; margin:0; }
  .detail dt { color:var(--doux); } .detail dd { margin:0; font-variant-numeric:tabular-nums; }
  .reserve { color:#ffbe8a; font-size:12.5px; margin-top:8px; }
  .legende div { display:flex; align-items:center; gap:8px; font-size:13px; margin:5px 0; }
  .pastille { width:13px; height:13px; border-radius:3px; border:2px solid; }
  a { color:#7cc0ff; }
  .liens { margin-top:18px; font-size:13px; display:flex; gap:14px; flex-wrap:wrap; }
  details { margin-top:16px; font-size:13px; color:var(--doux); }
  summary { cursor:pointer; color:var(--texte); font-weight:600; }
  details p, details li { line-height:1.55; }
  .bascule { position:absolute; top:12px; right:12px; z-index:3; display:flex; gap:6px; }
  .bascule button { background:#1a1d24dd; color:var(--texte); border:1px solid var(--bord);
                    padding:7px 12px; border-radius:7px; cursor:pointer; font-size:13px; }
  .bascule button.actif { background:var(--feu); border-color:var(--feu); color:#1a1005; }
  @media (max-width:760px){ .panneau{ position:relative; width:auto; height:52%; border-right:0;
    border-bottom:1px solid var(--bord);} #carte{ top:52%; } }
</style></head><body>
<div class="panneau">
  <h1>Brûlis</h1>
  <p class="accroche">Périmètres et surfaces brûlées de France, calculés depuis Sentinel-2.
  Open data, code ouvert.</p>
  <div class="avert"><b>Ce n'est pas un outil opérationnel.</b> Les images arrivent avec
  1 à 3 jours de retard. Pour une urgence, appelez le 18 ou le 112.</div>

  <h2>Feux publiés</h2>
  <div id="liste"></div>
  <div id="detail" class="detail"></div>

  <h2>Légende</h2>
  <div class="legende">
    <div><span class="pastille" style="border-color:var(--feu);background:#ff6b3533"></span>
      périmètre calculé</div>
    <div><span class="pastille" style="border-color:var(--isole);background:#ffd16633"></span>
      détection isolée — souvent une coupe forestière</div>
    <div><span class="pastille" style="border-color:var(--ems);background:transparent"></span>
      périmètre officiel Copernicus EMS (référence)</div>
  </div>

  <details><summary>Méthode et limites</summary>
    <p>Un indice de brûlure (NBR) est calculé sur deux images Sentinel-2, avant et après le
    feu, à partir du proche infrarouge et du moyen infrarouge (bandes B8A et B12, 20 m).
    Leur différence (dNBR) fait ressortir la végétation détruite. Au-delà d'un seuil, les
    pixels sont regroupés en polygones.</p>
    <p><b>Fiabilité mesurée.</b> Contre les périmètres officiels Copernicus EMS — tracés à
    la main sur des images à 0,5–1,5 m — le recouvrement est de 0,75 à 0,92 sur les quatre
    feux girondins de 2022, avec un réglage unique. Chaque feu affiche son propre écart.</p>
    <p><b>Ce que la méthode rate, et c'est mesuré :</b></p>
    <ul>
      <li>les <b>nuages et la fumée</b> masquent des zones entières — jusqu'à 14 % sur un
      des cas ; la part masquée est indiquée pour chaque feu ;</li>
      <li>un feu qui <b>rebrûle une zone brûlée récemment est invisible</b> : le sol est
      déjà sombre, il n'y a plus de contraste (dNBR médian −0,07, détection quasi nulle) ;</li>
      <li>les <b>coupes forestières</b> ressemblent à des brûlis : jusqu'à 17 % de la
      surface détectée sur un cas. D'où la couche « détections isolées », séparée.</li>
    </ul>
    <p><b>Latence.</b> Un satellite optique ne voit la zone qu'à son prochain passage sans
    nuages : 1 à 3 jours sur la France. Ce n'est pas un choix, c'est la physique.</p>
  </details>

  <div class="liens">
    <a href="data/feux.geojson" download>GeoJSON</a>
    <a href="data/feux.csv" download>CSV</a>
    <a href="data/isolees.geojson" download>Détections isolées</a>
  </div>
  <p style="color:var(--doux);font-size:12px;margin-top:14px">
    Fond : IGN Géoplateforme · Images : Copernicus Sentinel-2 · Référence : Copernicus EMS.
    Données produites sous Licence Ouverte 2.0.</p>
</div>

<div class="bascule">
  <button id="bPlan" class="actif">Plan</button>
  <button id="bOrtho">Photo</button>
  <button id="bEms">Référence EMS</button>
</div>
<div id="carte"></div>

<script src="vendor/maplibre-gl.js"></script>
<script>
const FEUX = __FEUX__, ISOLEES = __ISOLEES__, EMS = __EMS__;
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
  center:[-0.8,44.7], zoom:8
});
carte.addControl(new maplibregl.NavigationControl(), "bottom-right");
carte.addControl(new maplibregl.ScaleControl(), "bottom-left");

carte.on("load", () => {
  carte.addSource("ems", {type:"geojson", data:EMS});
  carte.addLayer({id:"ems-trait", type:"line", source:"ems", layout:{visibility:"none"},
    paint:{"line-color":"#4da3ff","line-width":1.6,"line-dasharray":[3,2]}});

  carte.addSource("isolees", {type:"geojson", data:ISOLEES});
  carte.addLayer({id:"isolees-fond", type:"fill", source:"isolees",
    paint:{"fill-color":"#ffd166","fill-opacity":0.22}});
  carte.addLayer({id:"isolees-trait", type:"line", source:"isolees",
    paint:{"line-color":"#ffd166","line-width":1}});

  carte.addSource("feux", {type:"geojson", data:FEUX});
  carte.addLayer({id:"feux-fond", type:"fill", source:"feux",
    paint:{"fill-color":"#ff6b35","fill-opacity":0.3}});
  carte.addLayer({id:"feux-trait", type:"line", source:"feux",
    paint:{"line-color":"#ff6b35","line-width":2}});

  if (FEUX.features.length) cadrer(FEUX);
  for (const c of ["feux-fond","isolees-fond"]) {
    carte.on("mouseenter", c, () => carte.getCanvas().style.cursor = "pointer");
    carte.on("mouseleave", c, () => carte.getCanvas().style.cursor = "");
  }
  carte.on("click", "feux-fond", e => choisir(
    FEUX.features.findIndex(f => f.properties.feu === e.features[0].properties.feu)));
  carte.on("click", "isolees-fond", e => new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(`<b>Détection isolée</b><br>${e.features[0].properties.surface_ha} ha<br>
      <span style="color:#8a6d3b">souvent une coupe forestière — à vérifier</span>`)
    .addTo(carte));
});

function bornes(fc){
  const b = new maplibregl.LngLatBounds();
  const parcourir = c => typeof c[0] === "number" ? b.extend(c) : c.forEach(parcourir);
  fc.features.forEach(f => parcourir(f.geometry.coordinates));
  return b;
}
function cadrer(fc){ carte.fitBounds(bornes(fc), {padding:{top:60,bottom:60,left:410,right:60}}); }

const liste = document.getElementById("liste"), detail = document.getElementById("detail");
liste.innerHTML = FEUX.features.map((f,i) => {
  const p = f.properties;
  return `<div class="feu" data-i="${i}"><span class="ha">${p.surface_ha.toLocaleString("fr-FR")} ha</span>
    <div class="nom">${p.feu}</div>
    <div class="meta">image du ${new Date(p.image_apres).toLocaleDateString("fr-FR")}</div></div>`;
}).join("") || '<p style="color:var(--doux)">Aucun feu publié pour le moment.</p>';

function choisir(i){
  if (i < 0) return;
  document.querySelectorAll(".feu").forEach((e,j) => e.classList.toggle("actif", j === i));
  const p = FEUX.features[i].properties;
  detail.innerHTML = `<dl>
    <dt>Surface</dt><dd><b>${p.surface_ha.toLocaleString("fr-FR")} ha</b></dd>
    <dt>Image avant</dt><dd>${p.image_avant}</dd>
    <dt>Image après</dt><dd>${p.image_apres} (${p.nuages_apres_pct.toFixed(0)} % nuages)</dd>
    <dt>Seuil dNBR</dt><dd>${p.seuil_dnbr}</dd>
    <dt>Référence EMS</dt><dd>${p.ems_surface_ha.toLocaleString("fr-FR")} ha (${p.ems_date})</dd>
    <dt>Recouvrement</dt><dd>${p.recouvrement_iou.toFixed(3)}</dd>
    <dt>Écart surface</dt><dd>${p.ecart_surface_pct > 0 ? "+" : ""}${p.ecart_surface_pct} %</dd>
  </dl>` + (p.reserves.length
    ? `<div class="reserve">⚠ ${p.reserves.join("<br>⚠ ")}</div>` : "");
  cadrer({features:[FEUX.features[i]]});
}
liste.onclick = e => { const c = e.target.closest(".feu"); if (c) choisir(+c.dataset.i); };

const bascule = (bouton, action) => document.getElementById(bouton).onclick = function(){
  this.classList.toggle("actif"); action(this.classList.contains("actif"));
};
document.getElementById("bPlan").onclick = () => basculerFond("plan");
document.getElementById("bOrtho").onclick = () => basculerFond("ortho");
function basculerFond(quel){
  for (const c of ["plan","ortho"])
    carte.setLayoutProperty(c, "visibility", c === quel ? "visible" : "none");
  document.getElementById("bPlan").classList.toggle("actif", quel === "plan");
  document.getElementById("bOrtho").classList.toggle("actif", quel === "ortho");
}
bascule("bEms", actif =>
  carte.setLayoutProperty("ems-trait", "visibility", actif ? "visible" : "none"));
</script></body></html>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", type=Path, default=Path("data/work"))
    p.add_argument("--ref", type=Path,
                   default=Path("data/reference/perimetres_ems_2022.geojson"))
    p.add_argument("--out", type=Path, default=Path("site"))
    args = p.parse_args()

    ref = gpd.read_file(args.ref)
    feux, isolees = [], []
    for dossier in sorted(d for d in args.work.iterdir() if d.is_dir()):
        r = construire(dossier, ref)
        if r:
            feux.append(r[0])
            isolees.extend(r[1])
            print(f"  {r[0]['properties']['feu']:22s} "
                  f"{r[0]['properties']['surface_ha']:8.1f} ha  "
                  f"+{len(r[1])} isolées")

    if not feux:
        print("Aucun feu à publier — lancer scripts/dnbr.py d'abord.")
        return 1

    feux.sort(key=lambda f: -f["properties"]["surface_ha"])
    donnees = args.out / "data"
    donnees.mkdir(parents=True, exist_ok=True)

    fc_feux = {"type": "FeatureCollection", "features": feux}
    fc_isol = {"type": "FeatureCollection", "features": isolees}
    utilises = {f["properties"]["ems_produit"] for f in feux}
    fc_ems = json.loads(ref[ref["produit"].isin(utilises)].to_json())

    (donnees / "feux.geojson").write_text(json.dumps(fc_feux), encoding="utf-8")
    (donnees / "isolees.geojson").write_text(json.dumps(fc_isol), encoding="utf-8")
    (donnees / "ems.geojson").write_text(json.dumps(fc_ems), encoding="utf-8")

    colonnes = ["feu", "surface_ha", "image_avant", "image_apres", "seuil_dnbr",
                "part_masquee", "ems_surface_ha", "ems_date", "recouvrement_iou",
                "ecart_surface_pct"]
    with (donnees / "feux.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colonnes, extrasaction="ignore")
        w.writeheader()
        w.writerows(f["properties"] for f in feux)

    html = (GABARIT
            .replace("__FEUX__", json.dumps(fc_feux, ensure_ascii=False))
            .replace("__ISOLEES__", json.dumps(fc_isol, ensure_ascii=False))
            .replace("__EMS__", json.dumps(fc_ems, ensure_ascii=False)))
    (args.out / "index.html").write_text(html, encoding="utf-8")

    poids = sum(f.stat().st_size for f in args.out.rglob("*")) / 1e6
    print(f"\n  {len(feux)} feux, {len(isolees)} détections isolées")
    print(f"  → {args.out}/  ({poids:.1f} Mo)   open {args.out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
