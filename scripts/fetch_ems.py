"""Télécharge les paquets vecteurs Copernicus EMS Rapid Mapping des feux girondins 2022.

Phase 0 — vérité terrain. Aucune authentification requise : les paquets sont servis
publiquement depuis le bucket S3 du portail mapping.emergency.copernicus.eu.

Usage : python scripts/fetch_ems.py [--dest data/reference/ems]

Le script est idempotent (ne retélécharge pas un fichier déjà présent et non vide) et
échoue bruyamment : un téléchargement qui rate est signalé, jamais contourné.
"""

import argparse
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://cems-mapping-website.s3.eu-west-1.amazonaws.com/static/activations"

# Activations retenues pour mesurer la fiabilité de la méthode. Un paquet = un produit vecteur.
# DEL = delineation (périmètre de l'événement), GRA = grading (sévérité),
# FEP = first estimate. Pour la vérité terrain « surface brûlée » on utilise le DEL
# le plus tardif (MONIT le plus élevé), les autres servent de comparaison.
ACTIVATIONS = {
    "EMSR592": {  # 16/07/2022 — Landiras I et La Teste-de-Buch (2 AOI)
        "label": "Gironde/Landes juillet 2022",
        "products": [
            "EMSR592_AOI01_DEL_PRODUCT_r1_RTP01_v1",
            "EMSR592_AOI01_DEL_MONIT01_r1_RTP01_v1",
            "EMSR592_AOI01_FEP_PRODUCT_r1_RTP01_v1",
            "EMSR592_AOI01_GRA_PRODUCT_r1_RTP01_v1",
            "EMSR592_AOI02_DEL_PRODUCT_r1_RTP01_v1",
            "EMSR592_AOI02_DEL_MONIT01_r1_RTP01_v1",
            "EMSR592_AOI02_DEL_MONIT02_r1_RTP01_v1",
            "EMSR592_AOI02_FEP_PRODUCT_r1_RTP01_v1",
            "EMSR592_AOI02_GRA_PRODUCT_r1_RTP01_v1",
        ],
    },
    "EMSR619": {  # 10/08/2022 — Landiras II
        "label": "Landiras août 2022",
        "products": [
            "EMSR619_AOI01_DEL_PRODUCT_r1_RTP01_v1",
            "EMSR619_AOI01_DEL_MONIT01_r1_RTP01_v1",
        ],
    },
    "EMSR633": {  # 14/09/2022 — Saumos
        "label": "Saumos septembre 2022",
        "products": [
            "EMSR633_AOI01_DEL_PRODUCT_r1_RTP01_v1",
            "EMSR633_AOI01_DEL_MONIT01_r1_VECTORS_v1",
            "EMSR633_AOI01_FEP_PRODUCT_r1_RTP01_v1",
            "EMSR633_AOI01_GRA_PRODUCT_r1_RTP01_v1",
        ],
    },
}


# Quelques produits ont un nom de carte PDF qui diffère du nom du paquet vecteur
# (révision de la carte seule). Relevé sur les pages d'activation.
PDF_OVERRIDES = {
    "EMSR633_AOI01_DEL_MONIT01_r1_VECTORS_v1": "EMSR633_AOI01_DEL_MONIT01_r1_RTP01_v2",
}


def download(url: str, dest: Path) -> bool:
    """Renvoie True si le fichier a été téléchargé, False s'il était déjà là."""
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
    tmp.rename(dest)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default="data/reference/ems", type=Path)
    args = parser.parse_args()

    failures = []
    for code, act in ACTIVATIONS.items():
        print(f"\n{code} — {act['label']}")
        for product in act["products"]:
            # La carte PDF accompagne le paquet vecteur : sa légende est la seule source
            # des dates d'acquisition des images (les shapefiles ne portent qu'un
            # dmg_src_id/or_src_id qui y renvoie). Indispensable pour comparer un dNBR
            # à un périmètre EMS sur la même date.
            pdf_base = PDF_OVERRIDES.get(product, product)
            for name in (f"{product}_vector.zip", f"{pdf_base}.pdf"):
                url = f"{BASE}/{code}/{name}"
                dest = args.dest / code / name
                try:
                    fetched = download(url, dest)
                except (urllib.error.URLError, OSError) as exc:
                    print(f"  ÉCHEC {name} : {exc}")
                    failures.append((name, str(exc)))
                    continue

                size = dest.stat().st_size
                status = "téléchargé" if fetched else "déjà présent"
                print(f"  {status:14s} {name}  ({size / 1024:.0f} Ko)")

                if not name.endswith(".zip"):
                    continue
                # Un zip corrompu doit être vu maintenant, pas au moment de l'analyse.
                try:
                    with zipfile.ZipFile(dest) as zf:
                        bad = zf.testzip()
                    if bad:
                        raise zipfile.BadZipFile(f"entrée corrompue : {bad}")
                except zipfile.BadZipFile as exc:
                    print(f"  ZIP INVALIDE {name} : {exc}")
                    failures.append((name, f"zip invalide : {exc}"))

    if failures:
        print(f"\n{len(failures)} échec(s) :")
        for name, err in failures:
            print(f"  - {name} : {err}")
        return 1

    print("\nTous les paquets vecteurs sont présents et valides.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
