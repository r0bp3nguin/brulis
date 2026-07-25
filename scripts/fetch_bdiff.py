"""Interroge la BDIFF (feux déclarés) et écrit les résultats en CSV.

La BDIFF n'expose pas d'API : le portail est un formulaire GET (`if[...]`) rendu en HTML.
On rejoue la même requête et on lit le tableau de résultats. Aucune donnée n'est inventée :
ce qui n'est pas dans la page n'est pas dans le CSV.

⚠️ Le serveur présente une chaîne TLS incomplète — voir `scripts/ca_bundle.py`, qui fournit
l'intermédiaire manquant après l'avoir vérifié. La vérification TLS reste active.

Rappel sur la donnée : la BDIFF est déclarative (SDIS/DDT), en surface communale, sans
géométrie. Elle sert de vérité de **surface**, jamais de vérité de **forme**.

Usage :
    python scripts/fetch_bdiff.py --annee 2022 --departement 29 --surface-min 100
    python scripts/fetch_bdiff.py --annee 2022 --departement 33 --surface-min 10 --surface-max 30
"""

import argparse
import csv
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from ca_bundle import contexte_ssl

BASE = "https://bdiff.agriculture.gouv.fr/incendies"
UA = "Mozilla/5.0 (compatible; brulis/0.1; projet open data feux de forêt)"

BALISES = re.compile(r"<[^>]+>")
LIGNES = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELLULES = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
SURFACE = re.compile(r"^([\d\s,.]+)")


def texte(html: str) -> str:
    return re.sub(r"\s+", " ", BALISES.sub("", html)).strip()


def nombre(brut: str) -> float | None:
    m = SURFACE.match(brut)
    if not m:
        return None
    try:
        return float(m.group(1).replace(" ", "").replace(",", ""))
    except ValueError:
        return None


def interroger(annee: int, departement: str | None, surface_min: float | None,
               surface_max: float | None) -> list[dict]:
    q = {
        "if[periodeAnnees][anneeDeb]": str(annee),
        "if[periodeAnnees][anneeFin]": str(annee),
    }
    if departement:
        q["if[deprts][value]"] = departement
    if surface_min is not None:
        q["if[surfaceDe]"] = str(surface_min)
        q["if[surfaceDeInc]"] = "1"
    if surface_max is not None:
        q["if[surfaceA]"] = str(surface_max)
        q["if[surfaceAInc]"] = "1"

    url = f"{BASE}?{urllib.parse.urlencode(q)}"
    print(f"  {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=contexte_ssl(), timeout=90) as r:
        html = r.read().decode("utf-8", "replace")

    annonce = re.findall(r"(\d[\d\s]*)\s*incendies?", html, re.I)
    total = annonce[0].strip() if annonce else "?"

    feux = []
    for ligne in LIGNES.findall(html):
        c = [texte(x) for x in CELLULES.findall(ligne)]
        if len(c) < 7 or not c[2].isdigit():
            continue
        # colonnes : _, actions, année, alerte, département, commune, surface, nature, précision
        commune = c[5]
        insee = re.search(r"INSEE\s*:\s*(\w+)", commune)
        feux.append({
            "annee": c[2],
            "alerte": c[3],
            "departement": c[4],
            "commune": re.sub(r"\s*INSEE.*$", "", commune).strip(),
            "insee": insee.group(1) if insee else "",
            "surface_ha": nombre(c[6]),
            "detail_surface": c[6],
            "nature": c[7] if len(c) > 7 else "",
            "precision": c[8] if len(c) > 8 else "",
        })

    # Le portail pagine à 10 lignes ; au-delà il faut affiner la requête plutôt que
    # de parcourir les pages (moins de charge pour le serveur, requête reproductible).
    if total not in ("?", "") and total.replace(" ", "").isdigit():
        n = int(total.replace(" ", ""))
        if n > len(feux):
            print(f"  ATTENTION : {n} résultats annoncés, {len(feux)} lus (page 1 seulement). "
                  "Affiner la requête (surface, département) pour tout récupérer.")
    return feux


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--annee", type=int, required=True)
    p.add_argument("--departement", help="code département, ex. 29")
    p.add_argument("--surface-min", type=float)
    p.add_argument("--surface-max", type=float)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    feux = interroger(args.annee, args.departement, args.surface_min, args.surface_max)
    if not feux:
        print("  aucun résultat")
        return 1

    feux.sort(key=lambda f: -(f["surface_ha"] or 0))
    for f in feux:
        print(f"  {f['alerte']:18s} {f['departement']:>3s} {f['commune'][:28]:30s} "
              f"{f['surface_ha'] or 0:10.2f} ha  {f['nature'][:26]}")

    out = args.out or Path(
        f"data/reference/bdiff_{args.annee}"
        f"{'_' + args.departement if args.departement else ''}.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(feux[0]))
        w.writeheader()
        w.writerows(feux)
    print(f"\n  {len(feux)} feux → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
