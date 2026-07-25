# Déploiement — Cloudflare Pages

Le site est entièrement statique : aucun serveur, aucune base, aucune variable
d'environnement côté hébergeur. Cloudflare ne sert que des fichiers.

**La clé FIRMS ne part jamais en ligne.** Elle ne sert qu'au calcul local (ou en CI), pas
à l'affichage : le site ne contient que des résultats déjà calculés. C'est pour ça qu'il
n'y a aucun secret à configurer côté Cloudflare.

## Déployer

```bash
make deploy          # construit le site, lance l'audit, puis publie
```

`make deploy` refuse de publier si l'audit échoue. Pour l'enchaînement manuel :

```bash
make site            # (re)construit site/
make verifier        # audit de publication
npx wrangler@latest pages deploy site --project-name brulis
```

Au premier lancement, `wrangler` ouvre le navigateur pour l'authentification Cloudflare et
propose de créer le projet. Ensuite, chaque déploiement est immédiat.

Nom du projet configurable : `make deploy PROJET=mon-nom`.

## Ce que l'audit vérifie

`make verifier` (`scripts/verifier_publication.py`) bloque la publication si :

1. une valeur de `.env` apparaît dans `site/`, dans un fichier suivi par Git, ou dans
   l'historique des commits ;
2. un motif de secret est présent dans les fichiers publiés (clé API, jeton GitHub ou AWS,
   clé privée, en-tête Bearer) ;
3. un chemin local (`/Users/...`) ou une adresse courriel personnelle y figure ;
4. `.env` n'est pas correctement ignoré par Git ;
5. une ressource est chargée depuis un domaine tiers non autorisé ;
6. le site est incomplet ou vide.

L'audit a été testé en conditions réelles : en y injectant volontairement la vraie clé, un
chemin local et un script CDN tiers, les trois sont détectés et la publication est
interrompue. Ce n'est pas un contrôle de façade.

## Sécurité de la page publiée

`site/_headers` est lu automatiquement par Cloudflare Pages.

- **CSP restrictive** : `default-src 'none'`, seules les tuiles IGN sont jointes.
  C'est la protection principale : même si une donnée malveillante se glissait dans un
  GeoJSON, elle ne pourrait ni s'exécuter ni sortir. `'unsafe-inline'` reste nécessaire
  (page en un fichier, sans build) ; `blob:` l'est pour les workers de MapLibre.
- `frame-ancestors 'none'` : pas d'inclusion dans une iframe tierce.
- `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, HSTS un an.
- `Permissions-Policy` : géolocalisation, caméra, micro et paiement désactivés.
- **CORS ouvert sur `/data/*` uniquement** — c'est voulu : les données sont réutilisables
  depuis n'importe quel site, c'est l'objet du projet.

**MapLibre est servi depuis `site/vendor/`**, pas depuis un CDN. Un CDN tiers voit les
visiteurs et peut altérer le code servi ; pour un site public, la dépendance est
rapatriée et versionnée dans le dépôt.

## Ce qui est réellement publié

| Fichier | Contenu |
|---|---|
| `index.html` | carte et interface, données incluses pour un affichage immédiat |
| `data/feux.geojson` | périmètres, surfaces, dates, écart à la référence EMS |
| `data/isolees.geojson` | détections isolées (souvent des coupes forestières) |
| `data/ems.geojson` | périmètres officiels Copernicus EMS, pour comparaison |
| `data/feux.csv` | mêmes attributs, format tableur |
| `vendor/` | MapLibre GL JS (BSD 3-Clause) |

Aucune donnée personnelle, aucun cookie, aucun traceur, aucun formulaire.

## Domaine

Cloudflare fournit `brulis.pages.dev`. Pour un domaine propre : *Workers & Pages → le
projet → Custom domains*. Le certificat est automatique.

## Mise à jour des données

```bash
make detecter        # feux en cours : FIRMS -> Sentinel-2 -> périmètres
make site
make deploy
```

Pour automatiser plus tard (GitHub Actions), la clé FIRMS se met dans les secrets du
dépôt, jamais dans un fichier. Le principe reste le même : la clé sert au calcul, jamais
à l'affichage.

## Limite connue

`index.html` embarque les données (~1,8 Mo) en plus de `data/`, ce qui les duplique. C'est
volontaire pour l'instant : la carte s'affiche sans attendre de requête. Si le nombre de
feux augmente sensiblement, il faudra passer à un chargement asynchrone, puis à des
PMTiles — l'audit alerte au-delà de 25 Mo.
