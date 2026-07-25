"""Isole la pile géospatiale des variables d'environnement d'une autre installation.

À importer AVANT rasterio / GDAL dans tout script du projet.

Motif : un PROJ_LIB, PROJ_DATA ou GDAL_DATA exporté par le shell (installation
Anaconda, Homebrew…) prend le pas sur les données embarquées dans le venv et fait
échouer la résolution des codes EPSG :

    CRSError: The EPSG code is unknown. PROJ: ... proj.db ... comes from another
    PROJ installation.

On ne conserve ces variables que si elles pointent à l'intérieur du venv courant.
"""

import os
import sys
from pathlib import Path

_VARIABLES = ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA")


def nettoyer_env() -> list[str]:
    """Retire les variables PROJ/GDAL étrangères au venv. Renvoie les noms retirés."""
    prefixe = Path(sys.prefix).resolve()
    retirees = []
    for var in _VARIABLES:
        valeur = os.environ.get(var)
        if not valeur:
            continue
        chemin = Path(valeur).resolve()
        if prefixe != chemin and prefixe not in chemin.parents:
            os.environ.pop(var)
            retirees.append(var)
    return retirees


nettoyer_env()
