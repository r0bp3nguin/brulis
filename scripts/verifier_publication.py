"""Audit avant mise en ligne : rien de sensible ne doit partir dans `site/`.

Lancé automatiquement par `make deploy`, et échoue bruyamment plutôt que de laisser
publier. Le dépôt et le site sont publics : une clé qui part en ligne est compromise, y
compris si on la retire ensuite (historique Git, caches, moteurs d'indexation).

Contrôles :
  1. aucune valeur de `.env` ne se retrouve dans `site/` ni dans un fichier suivi par Git ;
  2. aucun motif de secret (clé, jeton, mot de passe) dans les fichiers publiés ;
  3. aucun chemin local ni identité personnelle (nom d'utilisateur, courriel) ;
  4. `.env` bien ignoré par Git, et jamais entré dans l'historique ;
  5. aucune ressource chargée depuis un tiers non prévu (chaîne d'approvisionnement) ;
  6. le site contient bien ce qu'il doit contenir.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SITE = Path("site")
ENV = Path(".env")

# Domaines tiers admis dans les pages publiées. Tout autre appel sortant est signalé :
# une dépendance CDN, c'est un tiers qui voit les visiteurs et peut altérer le code.
# GoatCounter n'y figure pas volontairement : count.js est rapatrié dans vendor/, et si
# quelqu'un le rebranchait un jour sur gc.zgo.at, l'audit doit le refuser.
DOMAINES_AUTORISES = {"data.geopf.fr"}

# Ce que le navigateur va chercher tout seul — `src`, et `href` sur un `<link>`
# (feuille de style, préchargement). Un `<a href>` en est absent à dessein : c'est une
# navigation que le visiteur déclenche, pas un chargement. Les confondre faisait échouer
# la publication sur le moindre lien sortant (IGN, Copernicus, tableau de bord public).
CHARGEMENTS = [
    re.compile(r"\bsrc=[\"'](https?://([A-Za-z0-9.-]+)[^\"']*)"),
    re.compile(r"<link\b[^>]*?\bhref=[\"'](https?://([A-Za-z0-9.-]+)[^\"']*)", re.I),
]

MOTIFS_SECRET = [
    (re.compile(r"\b[A-Za-z0-9_-]*(?:api[_-]?key|apikey|map_key|secret|passwd|password)"
                r"[\"' ]*[:=][\"' ]*[A-Za-z0-9_\-]{12,}", re.I), "secret nommé avec valeur"),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "jeton GitHub"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "clé d'accès AWS"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "clé de type OpenAI"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "clé privée"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"), "jeton Bearer"),
]

MOTIFS_IDENTITE = [
    (re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"), "chemin local absolu"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "adresse courriel"),
]

# Extensions binaires : on ne les lit pas en texte (les tuiles, images, etc.).
BINAIRES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pmtiles", ".tif", ".woff2"}


class Audit:
    def __init__(self) -> None:
        self.erreurs: list[str] = []
        self.alertes: list[str] = []

    def erreur(self, m: str) -> None:
        self.erreurs.append(m)

    def alerte(self, m: str) -> None:
        self.alertes.append(m)


def valeurs_env() -> list[tuple[str, str]]:
    """(nom, valeur) des secrets réellement définis localement."""
    if not ENV.exists():
        return []
    out = []
    for ligne in ENV.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        nom, _, valeur = ligne.partition("=")
        valeur = valeur.strip().strip("\"'")
        if len(valeur) >= 8:  # en dessous, ce n'est pas un secret exploitable
            out.append((nom.strip(), valeur))
    return out


def fichiers_publies() -> list[Path]:
    return [f for f in SITE.rglob("*") if f.is_file()]


def lire(f: Path) -> str:
    if f.suffix.lower() in BINAIRES:
        return ""
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=60).stdout
    except Exception:
        return ""


def controler(audit: Audit) -> None:
    secrets = valeurs_env()
    publies = fichiers_publies()

    if not publies:
        audit.erreur("site/ est vide — lancer `make site` avant de déployer")
        return

    # 1. les secrets connus, là où ils ne doivent jamais être
    suivis = [Path(p) for p in git("ls-files").split() if Path(p).is_file()]
    for nom, valeur in secrets:
        for f in publies:
            if valeur in lire(f):
                audit.erreur(f"{nom} présent dans {f} — À NE PAS PUBLIER")
        for f in suivis:
            if valeur in lire(f):
                audit.erreur(f"{nom} présent dans le fichier suivi {f}")
        if valeur in git("log", "--all", "-S", valeur, "--oneline"):
            audit.erreur(f"{nom} apparaît dans l'historique Git")
    print(f"  {len(secrets)} secret(s) local(aux) vérifié(s) contre "
          f"{len(publies)} fichiers publiés et {len(suivis)} fichiers suivis")

    # 2 et 3. motifs génériques dans ce qui part en ligne
    for f in publies:
        contenu = lire(f)
        if not contenu:
            continue
        for motif, libelle in MOTIFS_SECRET:
            m = motif.search(contenu)
            if m:
                audit.erreur(f"{libelle} dans {f} : « {m.group(0)[:60]}… »")
        for motif, libelle in MOTIFS_IDENTITE:
            m = motif.search(contenu)
            if m:
                # Les adresses des producteurs de données sont publiques et légitimes.
                if libelle == "adresse courriel" and any(
                        d in m.group(0) for d in ("ems-copernicus.eu", "example.")):
                    continue
                audit.erreur(f"{libelle} dans {f} : « {m.group(0)[:60]} »")

    # 4. .env correctement exclu
    if ENV.exists():
        if not git("check-ignore", str(ENV)).strip():
            audit.erreur(".env n'est PAS ignoré par Git — corriger .gitignore d'abord")
        else:
            print("  .env présent et correctement ignoré par Git")
        # Comparaison ligne à ligne : une recherche de sous-chaîne matcherait
        # « .env.example », qui a justement vocation à être suivi.
        if ".env" in git("ls-files").split():
            audit.erreur(".env est suivi par Git")
    else:
        audit.alerte(".env absent : la détection FIRMS ne fonctionnera pas")

    # 5. dépendances externes chargées par les pages
    domaines = set()
    for f in publies:
        if f.suffix.lower() in {".html", ".js", ".css"}:
            domaines |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", lire(f)))
    # Les URL citées en texte (liens de documentation) ne sont pas des chargements.
    inattendus = {d for d in domaines if d not in DOMAINES_AUTORISES}
    for f in publies:
        if f.suffix.lower() != ".html":
            continue
        contenu = lire(f)
        for motif in CHARGEMENTS:
            for m in motif.finditer(contenu):
                if m.group(2) not in DOMAINES_AUTORISES:
                    audit.erreur(f"ressource chargée depuis un tiers : {m.group(1)[:70]} "
                                 f"({f}) — rapatrier en local")
    if inattendus:
        audit.alerte(f"domaines cités (liens, non chargés) : {', '.join(sorted(inattendus))}")
    print(f"  domaines chargés : {', '.join(sorted(DOMAINES_AUTORISES & domaines)) or 'aucun'}")

    # 6. le site est complet et exploitable
    for attendu in ("index.html", "data/feux.geojson"):
        if not (SITE / attendu).exists():
            audit.erreur(f"site/{attendu} manquant")
    feux = SITE / "data" / "feux.geojson"
    if feux.exists():
        try:
            n = len(json.loads(feux.read_text(encoding="utf-8"))["features"])
            print(f"  {n} feux publiés")
            if n == 0:
                audit.alerte("aucun feu publié : le site sera vide")
        except (json.JSONDecodeError, KeyError) as exc:
            audit.erreur(f"feux.geojson illisible : {exc}")

    poids = sum(f.stat().st_size for f in publies) / 1e6
    print(f"  poids total : {poids:.1f} Mo")
    if poids > 25:
        audit.alerte(f"{poids:.0f} Mo — envisager des PMTiles ou une simplification")


def main() -> int:
    if not SITE.exists():
        print("site/ absent — lancer `make site`")
        return 1

    print("Audit de publication\n")
    audit = Audit()
    controler(audit)

    print()
    for a in audit.alertes:
        print(f"  ATTENTION  {a}")
    for e in audit.erreurs:
        print(f"  ERREUR     {e}")

    if audit.erreurs:
        print(f"\n{len(audit.erreurs)} problème(s) bloquant(s) — publication interrompue.")
        return 1
    print("\nRien de sensible dans site/ : publication possible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
