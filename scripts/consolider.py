"""Consolide l'archive `data/feux/` : une fiche par incendie, pas une par jour.

Réparation d'une cause unique, corrigée depuis dans `detecter.py` (voir `JOURS_MEME_FEU`).
L'identifiant d'un feu était bâti sur la date du plus ancien point chaud encore présent
dans la fenêtre FIRMS glissante. Cette date avançant d'un jour chaque jour, un même
incendie ouvrait un dossier neuf chaque matin, et l'ancien n'était plus jamais rouvert :
publié tel quel, figé, avec un « prochain passage » périmé dès le lendemain. Chouppes (86)
en comptait trois, Grande-Synthe (59) cinq ; 25 des 40 fiches en attente annonçaient au
30/07/2026 une image pour une date déjà passée.

Le script regroupe les dossiers d'un même lieu qui décrivent un feu continu et n'en garde
qu'un : le plus ancien, parce qu'il porte la vraie date de départ, rempli du contenu qui
fait foi. Une mesure prime sur une estimation quelle que soit sa date — une image
exploitable ne se remplace pas par une emprise thermique.

Le journal des mises à jour est reporté au passage : il est indexé par identifiant, et
sans report il purgerait l'historique des fiches absorbées pour republier chaque feu
consolidé comme « nouveau ».

Le regroupement se fait sur le nom de dossier, à dessein. Un même incendie peut aussi se
retrouver éclaté sur plusieurs fiches parce que VIIRS en a fait deux foyers distincts —
c'est un autre cas, que `site.fusionner` traite déjà au moment de publier, avec un critère
de recouvrement plus prudent qu'un simple voisinage. Le confondre avec celui-ci reviendrait
à fondre en un seul feu les cicatrices jointives d'un complexe comme celui du Var.

Idempotent : relancé sur une archive déjà propre, il ne touche rien.

Usage :
    python scripts/consolider.py --verifier   # montre ce qui serait fusionné, n'écrit rien
    python scripts/consolider.py              # applique
"""

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from detecter import JOURS_MEME_FEU  # une seule règle de rattachement, pas deux


def lire(dossier: Path) -> dict | None:
    """Fiche archivée, augmentée de son dossier et de ses bornes de dates."""
    try:
        info = json.loads((dossier / "info.json").read_text(encoding="utf-8"))
        premier = str(info["premier_point_chaud"])[:10]
        date.fromisoformat(premier)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    info["_dossier"] = dossier
    info["_premier"] = premier
    info["_dernier"] = str(info.get("dernier_point_chaud") or premier)[:10]
    return info


def lieu_de(nom: str) -> str:
    """Partie « lieu » d'un nom de dossier : tout sauf la date de tête."""
    tete, _, reste = nom.partition("-")
    return reste if len(tete) == 8 and tete.isdigit() else nom


def chainer(fiches: list[dict]) -> list[list[dict]]:
    """Découpe les fiches d'un même lieu en incendies distincts.

    Deux fiches appartiennent au même feu si leurs périodes d'activité se suivent à moins
    de `JOURS_MEME_FEU`. Au-delà, le même lieu a simplement rebrûlé plus tard.
    """
    chaines: list[list[dict]] = []
    for f in sorted(fiches, key=lambda x: x["_premier"]):
        if chaines:
            fin = max(x["_dernier"] for x in chaines[-1])
            if (date.fromisoformat(f["_premier"])
                    - date.fromisoformat(fin)).days <= JOURS_MEME_FEU:
                chaines[-1].append(f)
                continue
        chaines.append([f])
    return chaines


def qui_fait_foi(chaine: list[dict]) -> dict:
    """La fiche retenue : une mesure prime sur une estimation, la plus récente sinon."""
    mesures = [f for f in chaine if f.get("statut") == "mesure"]
    return max(mesures or chaine, key=lambda f: f.get("calcule_le", ""))


def consolider(chaine: list[dict], appliquer: bool) -> dict:
    """Fond une chaîne de fiches en une seule et renvoie le compte rendu."""
    cible = min(chaine, key=lambda f: f["_premier"])
    retenue = qui_fait_foi(chaine)
    garder = ("perimetre.geojson" if retenue.get("statut") == "mesure"
              else "emprise.geojson")
    source = retenue["_dossier"] / garder
    if not source.exists():
        return {"cible": cible["_dossier"].name, "ignore": f"{garder} absent", "absorbes": []}

    info = {k: v for k, v in retenue.items() if not k.startswith("_")}
    info["id"] = cible["_dossier"].name
    # Le feu commence au premier point chaud jamais vu et finit au dernier : les fiches
    # intermédiaires ne voyaient chacune qu'une fenêtre de quatre jours.
    info["premier_point_chaud"] = min(f["_premier"] for f in chaine)
    info["dernier_point_chaud"] = max(f["_dernier"] for f in chaine)
    # La latence se compte depuis le départ réel du feu, pas depuis la fenêtre FIRMS qui a
    # produit la mesure : chaque fiche intermédiaire la sous-estimait d'autant de jours
    # qu'elle en avait perdu au début (Chouppes : 1 jour annoncé, 3 en réalité).
    image = (info.get("image_apres") or {}).get("date", "")
    if image:
        try:
            info["latence_jours"] = (date.fromisoformat(image)
                                     - date.fromisoformat(
                                         info["premier_point_chaud"])).days
        except ValueError:
            pass
    absorbes = [f["_dossier"].name for f in chaine if f is not cible]

    if appliquer:
        destination = cible["_dossier"]
        if source.resolve() != (destination / garder).resolve():
            shutil.copyfile(source, destination / garder)
        # Une fiche montre soit un périmètre mesuré, soit une emprise estimée, jamais les
        # deux : `site.py` choisit le fichier à lire d'après le statut.
        autre = "perimetre.geojson" if garder == "emprise.geojson" else "emprise.geojson"
        (destination / autre).unlink(missing_ok=True)
        (destination / "info.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        for f in chaine:
            if f is not cible:
                shutil.rmtree(f["_dossier"])

    return {"cible": cible["_dossier"].name, "retenue": retenue["_dossier"].name,
            "statut": info.get("statut"), "absorbes": absorbes,
            "debut": info["premier_point_chaud"], "fin": info["dernier_point_chaud"]}


def reporter_journal(chemin: Path, renommages: dict[str, str], appliquer: bool) -> int:
    """Reporte le journal des fiches absorbées sur celle qui subsiste.

    `historique.mettre_a_jour` purge les événements dont l'identifiant a disparu et traite
    tout identifiant inconnu comme un feu nouveau. Sans ce report, la consolidation
    effacerait l'historique de chaque feu concerné pour le réannoncer comme une découverte.
    """
    if not chemin.exists() or not renommages:
        return 0
    journal = json.loads(chemin.read_text(encoding="utf-8"))
    reportes = 0
    for evenement in journal.get("evenements", []):
        if evenement.get("id") in renommages:
            evenement["id"] = renommages[evenement["id"]]
            reportes += 1

    etat = journal.setdefault("etat", {})
    for ancien, survivant in renommages.items():
        etat.pop(ancien, None)
        # Surface remise à zéro plutôt que recopiée : la fiche consolidée ne se compare à
        # aucune surface antérieure de même nature (la précédente pouvait cumuler les
        # géométries de deux doublons du même feu). Zéro se lit « rien de comparable » —
        # `historique` s'abstient alors d'annoncer une progression qui n'a pas eu lieu, et
        # réinscrit la valeur réelle au même passage.
        etat.setdefault(survivant, {})["surface_ha"] = 0

    if appliquer:
        chemin.write_text(json.dumps(journal, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return reportes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feux", type=Path, default=Path("data/feux"))
    p.add_argument("--historique", type=Path, default=Path("site/data/historique.json"))
    p.add_argument("--verifier", action="store_true",
                   help="n'écrit rien, montre ce qui serait fusionné")
    args = p.parse_args()
    appliquer = not args.verifier

    if not args.feux.exists():
        print(f"{args.feux} absent — rien à consolider.")
        return 1

    par_lieu: dict[str, list[dict]] = {}
    illisibles = 0
    for dossier in sorted(args.feux.iterdir()):
        if not dossier.is_dir():
            continue
        fiche = lire(dossier)
        if fiche is None:
            illisibles += 1
            continue
        par_lieu.setdefault(lieu_de(dossier.name), []).append(fiche)

    total = sum(len(v) for v in par_lieu.values())
    comptes, renommages = [], {}
    for fiches in par_lieu.values():
        for chaine in chainer(fiches):
            if len(chaine) < 2:
                continue
            compte = consolider(chaine, appliquer)
            if compte.get("ignore"):
                print(f"  ! {compte['cible']} : {compte['ignore']} — chaîne laissée intacte")
                continue
            comptes.append(compte)
            for absorbe in compte["absorbes"]:
                renommages[absorbe] = compte["cible"]

    reportes = reporter_journal(args.historique, renommages, appliquer)

    if not comptes:
        print(f"  {total} fiches, une par incendie — rien à consolider.")
        return 0

    for c in sorted(comptes, key=lambda x: -len(x["absorbes"])):
        print(f"\n  {c['cible']}  [{c['statut']}]  {c['debut']} → {c['fin']}")
        print(f"     contenu retenu : {c['retenue']}")
        for absorbe in c["absorbes"]:
            print(f"     absorbe        : {absorbe}")

    verbe = "fusionnées" if appliquer else "à fusionner"
    print(f"\n  {len(comptes)} incendies reconstitués, "
          f"{sum(len(c['absorbes']) for c in comptes)} fiches en double {verbe} "
          f"({total} fiches avant, {total - sum(len(c['absorbes']) for c in comptes)} après)")
    print(f"  {reportes} événements du journal reportés sur la fiche subsistante")
    if illisibles:
        print(f"  {illisibles} dossiers sans info.json exploitable, laissés en place")
    if args.verifier:
        print("\n  (--verifier : rien n'a été écrit)")
    else:
        print("\n  → reconstruire le site : uv run python scripts/site.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
