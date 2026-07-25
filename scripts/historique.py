"""Journal des mises à jour : ce qui a changé d'un calcul au suivant.

Sans mémoire, le site ne peut pas dire « ce feu est nouveau » ni « son périmètre a
grandi » — or c'est l'information la plus utile à quelqu'un qui revient consulter la
carte. Ce module compare l'état courant à l'état publié précédemment et accumule les
événements.

Le fichier vit dans `site/data/` : il est publié avec le site et suivi par Git, donc il
survit aux reconstructions et aux changements de machine.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# En dessous, la variation relève du bruit de seuillage entre deux images, pas d'une
# progression du feu : l'annoncer comme une mise à jour serait trompeur.
VARIATION_SIGNIFICATIVE = 0.05

MAX_EVENEMENTS = 200


def charger(chemin: Path) -> dict:
    if chemin.exists():
        try:
            return json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"evenements": [], "etat": {}}


def mettre_a_jour(chemin: Path, feux: list[dict], horodatage: str) -> dict:
    """Compare `feux` à l'état mémorisé, enregistre les changements, renvoie le journal."""
    journal = charger(chemin)
    etat = journal.get("etat", {})
    nouveaux = []

    for f in feux:
        p = f["properties"]
        identifiant, surface = p["id"], p["surface_ha"]
        precedent = etat.get(identifiant)

        if precedent is None:
            nouveaux.append({
                "date": horodatage, "type": "nouveau", "id": identifiant,
                "feu": p["feu"], "departement": p.get("departement", ""),
                "surface_ha": surface, "image": p.get("image_apres", ""),
            })
        else:
            avant = precedent.get("surface_ha", 0)
            if avant > 0 and abs(surface - avant) / avant >= VARIATION_SIGNIFICATIVE:
                nouveaux.append({
                    "date": horodatage,
                    "type": "agrandi" if surface > avant else "revise",
                    "id": identifiant, "feu": p["feu"],
                    "departement": p.get("departement", ""),
                    "surface_ha": surface, "surface_precedente": avant,
                    "image": p.get("image_apres", ""),
                })

        etat[identifiant] = {"surface_ha": surface, "image": p.get("image_apres", ""),
                             "vu_le": horodatage}

    # Purge des feux disparus : une exécution antérieure a pu publier des foyers depuis
    # écartés (hors de France, sol nu, doublons). Garder leurs événements afficherait un
    # historique qui ne correspond à rien de consultable.
    vivants = {f["properties"]["id"] for f in feux}
    anciens = [e for e in journal.get("evenements", []) if e.get("id") in vivants]
    journal["evenements"] = (nouveaux + anciens)[:MAX_EVENEMENTS]
    journal["etat"] = {k: v for k, v in etat.items() if k in vivants}
    journal["derniere_execution"] = horodatage

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    return journal


def maintenant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
