"""Magasin de certificats complété pour les serveurs à chaîne TLS incomplète.

Cas rencontré : `bdiff.agriculture.gouv.fr` présente un intermédiaire qui n'est PAS
l'émetteur de son certificat (feuille émise par « GEANT TLS RSA 1 », intermédiaire servi
« GEANT OV RSA CA 4 »). Aucun client ne peut construire la chaîne — curl comme
Python/certifi échouent sur `unable to get local issuer certificate`.

Ce module **ne désactive rien**. Il télécharge l'intermédiaire manquant à l'URL que le
certificat lui-même publie (extension AIA), **vérifie qu'il chaîne bien vers une racine
déjà présente dans certifi**, et seulement alors l'ajoute à une copie locale du magasin.
La validation reste complète jusqu'à une racine de confiance ; on ne fait que fournir le
maillon que le serveur omet.

Intermédiaire concerné : « GEANT TLS RSA 1 », émis par « HARICA TLS RSA Root CA 2021 »
(présente dans certifi).

Usage :
    from ca_bundle import bundle_ca
    ctx = ssl.create_default_context(cafile=bundle_ca())
"""

import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

import certifi

# URL publiée par l'extension AIA (« CA Issuers ») du certificat de bdiff.
INTERMEDIAIRES = {
    "HARICA-GEANT-TLS-R1": "http://crt.harica.gr/HARICA-GEANT-TLS-R1.cer",
}

CACHE = Path("data/reference/ca-bundle.pem")


def _en_pem(brut: bytes) -> bytes:
    """Un .cer peut être DER ou déjà PEM."""
    if b"-----BEGIN CERTIFICATE-----" in brut:
        return brut
    return ssl.DER_cert_to_PEM_cert(brut).encode()


def _chaine_vers_racine_de_confiance(pem: bytes) -> bool:
    """Vrai si le certificat se valide contre le magasin certifi d'origine."""
    res = subprocess.run(
        ["openssl", "verify", "-CAfile", certifi.where(), "/dev/stdin"],
        input=pem, capture_output=True,
    )
    return res.returncode == 0


def bundle_ca(cache: Path = CACHE, forcer: bool = False) -> str:
    """Chemin d'un magasin = certifi + intermédiaires manquants vérifiés."""
    if cache.exists() and not forcer:
        return str(cache)

    morceaux = [Path(certifi.where()).read_bytes()]
    for nom, url in INTERMEDIAIRES.items():
        with urllib.request.urlopen(url, timeout=60) as resp:
            pem = _en_pem(resp.read())
        if not _chaine_vers_racine_de_confiance(pem):
            raise SystemExit(
                f"{nom} ne se valide pas contre certifi : on ne l'ajoute pas. "
                "Vérifier la source avant d'aller plus loin."
            )
        morceaux.append(pem)
        print(f"  intermédiaire vérifié et ajouté : {nom}")

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"\n".join(morceaux))
    return str(cache)


def contexte_ssl() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=bundle_ca())


if __name__ == "__main__":
    chemin = bundle_ca(forcer="--forcer" in sys.argv)
    print(f"magasin : {chemin}")
    with urllib.request.urlopen(
        "https://bdiff.agriculture.gouv.fr/", context=contexte_ssl(), timeout=60
    ) as r:
        print(f"bdiff.agriculture.gouv.fr : HTTP {r.status}, "
              f"chaîne TLS validée jusqu'à une racine de confiance")
