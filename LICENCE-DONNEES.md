# Licence des données

Le dépôt mêle deux natures juridiques, volontairement séparées.

| Quoi | Licence |
|---|---|
| Code (`scripts/`, `Makefile`, workflow) | **MIT** — voir `LICENSE` |
| Données produites (périmètres, surfaces, CSV, GeoJSON) | **Licence Ouverte 2.0** (Etalab) |
| MapLibre GL JS (`site/vendor/`) | BSD 3-Clause, © MapLibre contributors |

## Données produites — Licence Ouverte 2.0

Les périmètres, surfaces et fichiers publiés dans `site/data/` et `data/feux/` sont
réutilisables librement, y compris commercialement, sous **Licence Ouverte 2.0** :
<https://www.etalab.gouv.fr/licence-ouverte-open-licence/>

La seule obligation est la **paternité** : mentionner l'origine et la date de la version
utilisée. Par exemple :

> Périmètres de zones brûlées — Brûlis, d'après Copernicus Sentinel-2 et NASA FIRMS,
> version du 27/07/2026.

La Licence Ouverte a été retenue de préférence à l'ODbL : cette dernière impose le partage
à l'identique des bases dérivées, ce qui freine la reprise par une administration ou une
rédaction. L'objectif du projet étant que ces périmètres soient repris, la contrainte
serait contre-productive.

## Sources amont et leurs conditions

Toutes les sources sont ouvertes, et aucune n'interdit les produits dérivés.

- **Copernicus Sentinel-2** (images) — politique de données Copernicus : accès libre,
  entier et gratuit, produits dérivés autorisés. Attribution : « Contient des données
  Copernicus Sentinel modifiées (2026) ».
- **NASA FIRMS / VIIRS** (points chauds) — données NASA, réutilisation libre.
  Attribution : « NASA FIRMS ».
- **IGN Géoplateforme** (fond de carte, référentiel des communes) — Licence Ouverte 2.0.
- **Copernicus EMS Rapid Mapping** (périmètres officiels 2022, utilisés pour mesurer la
  fiabilité de la méthode) — réutilisation libre avec attribution © Union européenne.
- **BDIFF** (surfaces déclarées, comparaison) — ministère de l'Agriculture, Licence Ouverte.

## Ce que la licence ne couvre pas

Une licence autorise la réutilisation ; elle ne garantit pas l'exactitude. Les limites
mesurées de la méthode sont documentées dans `docs/phase0-resultats.md` et résumées sur la
page publique : trous de couverture nuageuse, sur-brûlage indétectable, confusion possible
avec des coupes forestières, et surfaces « estimées » qui sont des ordres de grandeur, pas
des mesures.

**Ce produit n'est pas un outil opérationnel de lutte contre l'incendie** et ne doit pas
être utilisé comme tel.
