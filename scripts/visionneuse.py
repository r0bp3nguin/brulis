"""Génère une visionneuse HTML autonome pour vérifier les détections à l'œil.

Outil de **contrôle Phase 0**, pas le produit. La carte publique (Phase 2) sera un site
statique MapLibre + PMTiles, après les portes G0 et G1 — voir la feuille de route.

Contraintes assumées :
  - **un seul fichier**, ouvert par double-clic. Donc tout est embarqué en data URI :
    en `file://`, un `fetch()` de GeoJSON voisin est bloqué par la politique d'origine.
  - **aucune dépendance JS**. Le rendu est un canvas d'une centaine de lignes plutôt
    qu'une bibliothèque cartographique : rien à mettre à jour dans six mois, et cet outil
    est jetable par construction.

Toute la géométrie est projetée **côté Python** dans le repère pixel de l'image du cas.
Le JavaScript ne fait donc aucun calcul de projection : il applique une translation et
un facteur d'échelle, rien de plus.

Usage :
    python scripts/visionneuse.py
    open data/work/visionneuse.html
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import env_geo  # noqa: F401  — doit précéder rasterio/GDAL
import geopandas as gpd
import numpy as np
import rasterio
from matplotlib import cm, colors
from PIL import Image
from pystac_client import Client

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
CRS_METRIQUE = 2154
COTE_MAX = 1300  # px : compromis lisibilité / poids du fichier
DNBR_MIN, DNBR_MAX = -0.1, 0.8


def _data_uri(img: Image.Image, fmt: str, qualite: int = 82) -> str:
    tampon = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(tampon, "JPEG", quality=qualite, optimize=True)
        mime = "image/jpeg"
    else:
        img.save(tampon, "PNG", optimize=True)
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(tampon.getvalue()).decode()}"


def image_couleur(item_id: str, bounds, crs_cible, taille) -> str | None:
    """Composition colorée Sentinel-2, recadrée et sous-échantillonnée."""
    got = list(Client.open(STAC).search(collections=[COLLECTION], ids=[item_id]).items())
    if not got:
        return None
    with rasterio.open(got[0].assets["visual"].href) as ds:
        if ds.crs != crs_cible:
            return None
        win = ds.window(*bounds).round_offsets().round_lengths()
        arr = ds.read(window=win, boundless=True, fill_value=0,
                      out_shape=(3, taille[1], taille[0]))
    return _data_uri(Image.fromarray(np.transpose(arr, (1, 2, 0)).astype("uint8")), "JPEG")


def image_dnbr(dnbr: np.ndarray, taille) -> str:
    """dNBR colorisé ; les pixels écartés (nuage, eau, hors image) restent transparents."""
    norme = colors.Normalize(vmin=DNBR_MIN, vmax=DNBR_MAX, clip=True)
    rgba = (cm.inferno(norme(np.nan_to_num(dnbr, nan=DNBR_MIN))) * 255).astype("uint8")
    rgba[..., 3] = np.where(np.isnan(dnbr), 0, 255)
    return _data_uri(
        Image.fromarray(rgba, "RGBA").resize(taille, Image.Resampling.BILINEAR), "PNG"
    )


def en_pixels(geom, transform, echelle_x, echelle_y):
    """Géométrie projetée -> anneaux en coordonnées pixel de l'image affichée."""
    inverse = ~transform
    anneaux = []
    parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for poly in parts:
        if poly.is_empty:
            continue
        for ligne in [poly.exterior, *poly.interiors]:
            pts = []
            for x, y in ligne.coords:
                col, row = inverse * (x, y)
                pts.append([round(col * echelle_x, 1), round(row * echelle_y, 1)])
            if len(pts) > 2:
                anneaux.append(pts)
    return anneaux


def construire_cas(dossier: Path, ref: gpd.GeoDataFrame) -> dict | None:
    polys_path = dossier / "polygones.geojson"
    if not polys_path.exists():
        return None
    metriques = json.loads((dossier / "metriques.json").read_text(encoding="utf-8"))

    with rasterio.open(dossier / "dnbr.tif") as ds:
        dnbr = ds.read(1)
        transform, crs = ds.transform, ds.crs
        bounds = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
    h, w = dnbr.shape

    facteur = min(1.0, COTE_MAX / max(h, w))
    taille = (max(1, int(w * facteur)), max(1, int(h * facteur)))
    ex, ey = taille[0] / w, taille[1] / h

    verite = ref[ref["produit"] == metriques["produit_verite"]].to_crs(crs)
    verite_geom = verite.union_all()
    polys = gpd.read_file(polys_path).to_crs(crs)

    # Même règle que faux_positifs.py : « touche la vérité » = sur le feu.
    touche = polys.intersects(verite_geom)
    surfaces = polys.to_crs(CRS_METRIQUE).area / 1e4

    detections = [
        {"anneaux": en_pixels(g, transform, ex, ey),
         "ha": round(float(s), 2), "hors": bool(not t)}
        for g, s, t in zip(polys.geometry, surfaces, touche)
    ]

    seuil = metriques.get("seuil_retenu")
    ligne = next((r for r in metriques["resultats"] if abs(r["seuil"] - seuil) < 1e-9),
                 metriques["meilleur_seuil"])

    return {
        "nom": metriques["feu"],
        "largeur": taille[0], "hauteur": taille[1],
        "couleur": image_couleur(metriques["image_apres"]["id"], bounds, crs, taille),
        "dnbr": image_dnbr(dnbr, taille),
        "verite": en_pixels(verite_geom, transform, ex, ey),
        "detections": detections,
        "info": {
            "seuil": seuil,
            "iou": ligne["iou"], "rappel": ligne["rappel"], "precision": ligne["precision"],
            "ecart": ligne["ecart_surface_pct"],
            "ha_verite": metriques["surface_verite_ha"],
            "ha_detecte": ligne["surface_detectee_ha"],
            "plafond": metriques.get("plafond_rappel"),
            "date_ems": metriques["date_situation_ems"],
            "capteur_ems": metriques["capteur_ems"],
            "avant": metriques["image_avant"]["date"],
            "apres": metriques["image_apres"]["date"],
            "reserves": metriques.get("reserves_ems") or "aucune",
            "n_hors": sum(1 for d in detections if d["hors"]),
            "ha_hors": round(sum(d["ha"] for d in detections if d["hors"]), 1),
            # Le périmètre EMS est dessiné en entier, alors que les métriques ont pu être
            # calculées sur une zone réduite (cicatrice antérieure exclue). Sans cette
            # mention, le rouge à l'écran et l'IoU de l'en-tête ne parlent pas de la même
            # surface — et la zone rebrûlée, justement indétectable, passerait pour une
            # omission de la méthode.
            "note": (
                f"périmètre EMS affiché en entier ({verite.to_crs(CRS_METRIQUE).area.sum() / 1e4:.0f} ha) ; "
                f"métriques calculées sur {metriques['surface_verite_ha']} ha, "
                "cicatrice antérieure exclue (sur-brûlage invisible au dNBR)"
                if metriques.get("produits_exclus") else ""
            ),
        },
    }


GABARIT = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Brûlis — visionneuse de vérification</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
         background:#14161a; color:#e8eaed; }
  header { padding:10px 16px; border-bottom:1px solid #2a2e35; background:#191c21;
           display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  select { background:#22262d; color:#e8eaed; border:1px solid #3a3f48; border-radius:6px;
           padding:6px 10px; font-size:14px; }
  .bascules { display:flex; gap:14px; flex-wrap:wrap; }
  label { display:flex; gap:6px; align-items:center; cursor:pointer; user-select:none; }
  .pastille { width:11px; height:11px; border-radius:2px; display:inline-block; }
  .metriques { margin-left:auto; display:flex; gap:16px; font-variant-numeric:tabular-nums;
               flex-wrap:wrap; }
  .metriques b { color:#fff; font-weight:600; }
  .avert { padding:6px 16px; background:#3a2f12; border-bottom:1px solid #5a4a1d;
          color:#ffd166; font-size:12.5px; display:none; }
  .sous { padding:6px 16px; background:#121418; border-bottom:1px solid #2a2e35;
          color:#9aa1ab; font-size:12.5px; }
  #toile { display:block; cursor:grab; touch-action:none; }
  #toile.glisse { cursor:grabbing; }
  #infobulle { position:fixed; pointer-events:none; background:#0d0f12ee; padding:6px 10px;
               border:1px solid #3a3f48; border-radius:6px; font-size:13px; display:none; }
  footer { position:fixed; bottom:0; left:0; right:0; padding:5px 16px; font-size:12px;
           color:#7d848e; background:#12141899; }
</style></head><body>
<header>
  <select id="cas"></select>
  <div class="bascules" id="bascules"></div>
  <div class="metriques" id="metriques"></div>
</header>
<div class="sous" id="contexte"></div>
<div class="avert" id="avertissement"></div>
<canvas id="toile"></canvas>
<div id="infobulle"></div>
<footer>molette = zoom · glisser = déplacer · double-clic = réinitialiser · clic sur un
polygone = sa surface — outil de vérification Phase 0, pas le produit publié</footer>
<script>
const CAS = __DONNEES__;
const COUCHES = [
  {cle:"couleur",    nom:"Sentinel-2",  couleur:"#8d99ae", actif:true},
  {cle:"dnbr",       nom:"dNBR",        couleur:"#f0932b", actif:false},
  {cle:"verite",     nom:"périmètre EMS", couleur:"#d62728", actif:true},
  {cle:"detections", nom:"détections",  couleur:"#4da3ff", actif:true},
];
const etat = {i:0, x:0, y:0, k:1, actifs:{}};
COUCHES.forEach(c => etat.actifs[c.cle] = c.actif);

const toile = document.getElementById("toile"), ctx = toile.getContext("2d");
const images = {};

function cas(){ return CAS[etat.i]; }

function chargerImages(){
  const c = cas();
  ["couleur","dnbr"].forEach(cle => {
    if (!c[cle]) return;
    const k = etat.i + ":" + cle;
    if (images[k]) return;
    const im = new Image();
    im.onload = dessiner;
    im.src = c[cle];
    images[k] = im;
  });
}

function ajuster(){
  toile.width = window.innerWidth;
  toile.height = window.innerHeight - toile.getBoundingClientRect().top - 26;
  recentrer();
}

function recentrer(){
  const c = cas();
  etat.k = Math.min(toile.width / c.largeur, toile.height / c.hauteur) * 0.96;
  etat.x = (toile.width - c.largeur * etat.k) / 2;
  etat.y = (toile.height - c.hauteur * etat.k) / 2;
  dessiner();
}

function tracer(anneaux, trait, epaisseur, remplissage){
  ctx.beginPath();
  for (const anneau of anneaux){
    ctx.moveTo(anneau[0][0], anneau[0][1]);
    for (let j = 1; j < anneau.length; j++) ctx.lineTo(anneau[j][0], anneau[j][1]);
    ctx.closePath();
  }
  if (remplissage){ ctx.fillStyle = remplissage; ctx.fill("evenodd"); }
  ctx.strokeStyle = trait;
  ctx.lineWidth = epaisseur / etat.k;
  ctx.stroke();
}

function dessiner(){
  const c = cas();
  ctx.fillStyle = "#0b0d10";
  ctx.fillRect(0, 0, toile.width, toile.height);
  ctx.save();
  ctx.translate(etat.x, etat.y);
  ctx.scale(etat.k, etat.k);

  for (const cle of ["couleur","dnbr"]){
    const im = images[etat.i + ":" + cle];
    if (etat.actifs[cle] && im && im.complete) ctx.drawImage(im, 0, 0, c.largeur, c.hauteur);
  }
  if (etat.actifs.detections)
    for (const d of c.detections)
      tracer(d.anneaux, d.hors ? "#ffd166" : "#4da3ff", 1.4,
             d.hors ? "#ffd16622" : "#4da3ff22");
  if (etat.actifs.verite) tracer(c.verite, "#d62728", 2, null);
  ctx.restore();
}

function versImage(ev){
  const r = toile.getBoundingClientRect();
  return [(ev.clientX - r.left - etat.x) / etat.k, (ev.clientY - r.top - etat.y) / etat.k];
}

function dansAnneaux(anneaux, px, py){
  let dedans = false;
  for (const a of anneaux)
    for (let i = 0, j = a.length - 1; i < a.length; j = i++){
      const [xi, yi] = a[i], [xj, yj] = a[j];
      if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi)
        dedans = !dedans;
    }
  return dedans;
}

const infobulle = document.getElementById("infobulle");
toile.addEventListener("click", ev => {
  const [px, py] = versImage(ev);
  const t = cas().detections.find(d => dansAnneaux(d.anneaux, px, py));
  if (!t){ infobulle.style.display = "none"; return; }
  infobulle.innerHTML = `<b>${t.ha.toFixed(2)} ha</b> — ` +
    (t.hors ? '<span style="color:#ffd166">hors périmètre EMS</span>'
            : '<span style="color:#4da3ff">sur le feu</span>');
  infobulle.style.display = "block";
  infobulle.style.left = (ev.clientX + 14) + "px";
  infobulle.style.top = (ev.clientY + 14) + "px";
});

let glisse = null;
toile.addEventListener("pointerdown", ev => {
  glisse = [ev.clientX - etat.x, ev.clientY - etat.y];
  toile.classList.add("glisse"); toile.setPointerCapture(ev.pointerId);
});
toile.addEventListener("pointermove", ev => {
  if (!glisse) return;
  etat.x = ev.clientX - glisse[0]; etat.y = ev.clientY - glisse[1]; dessiner();
});
toile.addEventListener("pointerup", () => { glisse = null; toile.classList.remove("glisse"); });
toile.addEventListener("dblclick", recentrer);
toile.addEventListener("wheel", ev => {
  ev.preventDefault();
  const [px, py] = versImage(ev);
  etat.k *= Math.exp(-ev.deltaY * 0.0016);
  // Le point sous le curseur reste fixe : on recale l'origine après le zoom.
  const r = toile.getBoundingClientRect();
  etat.x = ev.clientX - r.left - px * etat.k;
  etat.y = ev.clientY - r.top - py * etat.k;
  dessiner();
}, {passive:false});

function entete(){
  const c = cas(), i = c.info;
  document.getElementById("metriques").innerHTML =
    `<span>IoU <b>${i.iou.toFixed(3)}</b></span>` +
    `<span>rappel <b>${i.rappel.toFixed(3)}</b></span>` +
    `<span>précision <b>${i.precision.toFixed(3)}</b></span>` +
    `<span>écart surface <b>${i.ecart > 0 ? "+" : ""}${i.ecart.toFixed(1)} %</b></span>`;
  document.getElementById("contexte").textContent =
    `seuil ${i.seuil.toFixed(2)} · vérité EMS ${i.date_ems} (${i.capteur_ems}) ` +
    `${i.ha_verite} ha · détecté ${i.ha_detecte} ha · Sentinel-2 ${i.avant} → ${i.apres} · ` +
    `hors périmètre : ${i.n_hors} polygones, ${i.ha_hors} ha · ` +
    `plafond du rappel (nuages) ${i.plafond != null ? i.plafond.toFixed(3) : "?"} · ` +
    `réserves EMS : ${i.reserves}`;
  const avert = document.getElementById("avertissement");
  avert.textContent = i.note || "";
  avert.style.display = i.note ? "block" : "none";
}

const selecteur = document.getElementById("cas");
CAS.forEach((c, i) => selecteur.add(new Option(c.nom, i)));
selecteur.onchange = () => {
  etat.i = +selecteur.value; chargerImages(); entete(); recentrer();
};

document.getElementById("bascules").innerHTML = COUCHES.map(c =>
  `<label><input type="checkbox" data-cle="${c.cle}" ${c.actif ? "checked" : ""}>` +
  `<span class="pastille" style="background:${c.couleur}"></span>${c.nom}</label>`).join("");
document.querySelectorAll("#bascules input").forEach(i =>
  i.onchange = () => { etat.actifs[i.dataset.cle] = i.checked; dessiner(); });

window.addEventListener("resize", ajuster);
chargerImages(); entete(); ajuster();
</script></body></html>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", type=Path, default=Path("data/work"))
    p.add_argument("--ref", type=Path,
                   default=Path("data/reference/perimetres_ems_2022.geojson"))
    p.add_argument("--out", type=Path, default=Path("data/work/visionneuse.html"))
    args = p.parse_args()

    ref = gpd.read_file(args.ref)
    cas = []
    for dossier in sorted(d for d in args.work.iterdir() if d.is_dir()):
        c = construire_cas(dossier, ref)
        if c:
            cas.append(c)
            print(f"  {c['nom']:22s} {c['largeur']}×{c['hauteur']} px, "
                  f"{len(c['detections'])} polygones")

    if not cas:
        print("Aucun cas — lancer scripts/dnbr.py d'abord.")
        return 1

    cas.sort(key=lambda c: c["nom"])
    html = GABARIT.replace("__DONNEES__", json.dumps(cas, ensure_ascii=False))
    args.out.write_text(html, encoding="utf-8")
    print(f"\n  {len(cas)} cas → {args.out}  ({args.out.stat().st_size / 1e6:.1f} Mo)")
    print(f"  open {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
