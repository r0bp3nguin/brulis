"""Extrait le contexte d'acquisition de chaque produit EMS depuis la légende des cartes PDF.

Pourquoi : les shapefiles EMS ne portent qu'un identifiant de source (`dmg_src_id`).
Le capteur, la date d'acquisition post-événement et les réserves de l'opérateur
(fumée, nuages, analyse incomplète) ne figurent que dans la légende de la carte PDF.

Sans ces dates, comparer un dNBR Sentinel-2 à un périmètre EMS n'a pas de sens : on
comparerait deux instants différents d'un feu en cours. C'est la base du protocole de
la validation de la méthode.

Sortie : data/reference/ems_contexte.csv + résumé lisible sur stdout.
Dépend de `pdftotext` (poppler).
"""

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

# La légende est en colonnes ; pdftotext -layout intercale les colonnes sur une même
# ligne. On normalise tout en un seul flux de texte puis on repère les motifs.
PATTERNS = {
    "image_post": re.compile(
        r"Post-event image:\s*(.+?)(?=Pre-event image:|Base vector layers:|$)", re.S
    ),
    "image_pre": re.compile(
        r"Pre-event image:\s*(.+?)(?=Post-event image:|Base vector layers:|$)", re.S
    ),
    "situation": re.compile(r"Situation as of\s+(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?)"),
    "evenement": re.compile(r"Event\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})"),
    "production": re.compile(r"Map production\s+(\d{2}/\d{2}/\d{4})"),
    "echelle": re.compile(r"scale of analysis is\s*(1:\d+)"),
    "mmu": re.compile(r"minimum mapping unit \(MMU\) is\s*([\d\s]+sq m)"),
    "rmse": re.compile(r"geometric accuracy\s*\(RMSE\) is\s*(.+?)[,.]"),
}

# Réserves de l'opérateur qui limitent la valeur du produit comme vérité terrain.
CAVEATS = [
    (r"delineation is not complete", "délimitation incomplète"),
    (r"dense smoke", "fumée dense"),
    (r"cloud cover(?:age)? (?:in|over) (?:the )?[Aa]o[Ii]", "couverture nuageuse en AOI"),
    (r"could not be analysed", "zones non analysées"),
    (r"cumulates all burnt area extents", "cumule les produits précédents"),
    (r"fire (?:was |is )?still (?:active|ongoing|really dynamic)", "feu encore actif"),
]

DATE_IMG = re.compile(r"acquired on\s*(\d{2}/\d{2}/\d{4})(?:\s*at\s*(\d{2}:\d{2}))?")
# Le nom du capteur varie d'une carte à l'autre (SPOT6, SPOT6/7, Pléiades-1A/B…).
SENSOR = re.compile(
    r"(SPOT[-\s]?\d(?:/\d)?|Sentinel[-\s]?\d[AB]?|Pl[ée]iades(?:-1A/B)?|Landsat[-\s]?\d(?:/\d)?"
    r"|WorldView[-\s]?\d?|GeoEye[-\s]?\d?|PlanetScope|Kompsat[-\s]?\d?)",
    re.I,
)


def page1_text(pdf: Path) -> str:
    """Texte de la première page (la carte tient sur une page), aplati."""
    out = subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf), "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    return re.sub(r"\s+", " ", out)


def parse(pdf: Path) -> dict:
    txt = page1_text(pdf)
    rec = {"produit": pdf.stem}

    for key, pat in PATTERNS.items():
        m = pat.search(txt)
        rec[key] = re.sub(r"\s+", " ", m.group(1)).strip()[:400] if m else ""

    for slot in ("image_post", "image_pre"):
        blob = rec.pop(slot)
        # Le nom du capteur suit immédiatement l'étiquette ; au-delà, le texte des
        # colonnes voisines s'intercale (pdftotext -layout aplatit la mise en page).
        sensors = {m.group(1).replace(" ", "-") for m in SENSOR.finditer(blob[:160])}
        dates = DATE_IMG.findall(blob)
        rec[f"{slot}_capteur"] = " + ".join(sorted(sensors))
        rec[f"{slot}_dates"] = " ; ".join(
            f"{d}{' ' + h if h else ''}" for d, h in dates
        )

    rec["reserves"] = " ; ".join(
        label for pat, label in CAVEATS if re.search(pat, txt, re.I)
    )
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="data/reference/ems", type=Path)
    parser.add_argument("--out", default="data/reference/ems_contexte.csv", type=Path)
    args = parser.parse_args()

    pdfs = sorted(args.src.glob("*/*.pdf"))
    if not pdfs:
        print(f"Aucun PDF sous {args.src} — lancer scripts/fetch_ems.py d'abord.")
        return 1

    rows = [parse(p) for p in pdfs]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    for r in rows:
        print(f"\n{r['produit']}")
        print(f"  situation      {r['situation'] or '?'}   (production {r['production'] or '?'})")
        print(f"  image post     {r['image_post_capteur'] or '?'}  —  {r['image_post_dates'] or '?'}")
        print(f"  image pré      {r['image_pre_capteur'] or '?'}  —  {r['image_pre_dates'] or '?'}")
        print(f"  analyse        échelle {r['echelle'] or '?'}, MMU {r['mmu'] or '?'}, RMSE {r['rmse'] or '?'}")
        if r["reserves"]:
            print(f"  RÉSERVES       {r['reserves']}")

    print(f"\n{len(rows)} produits → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
