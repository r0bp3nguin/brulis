# Brûlis — les zones brûlées de France, en accès libre

Brûlis publie **où ça brûle** et **ce qui a brûlé** en France, sous forme de cartes et de
fichiers réutilisables par tout le monde, gratuitement.

Pour chaque incendie détecté, vous obtenez le contour géographique daté de la zone touchée et
une estimation de la surface parcourue par le feu — pas seulement un total par commune, mais la
forme réelle du sinistre, exportable en GeoJSON et en CSV.

> **Brûlis n'est pas un outil de secours.** Les données arrivent avec plusieurs heures à
> plusieurs jours de retard. En cas d'incendie, appelez le **18** ou le **112**.

## Pourquoi ce projet existe

Quand un incendie ravage plusieurs centaines d'hectares, il faut aujourd'hui attendre l'année
suivante pour disposer d'un bilan officiel — et celui-ci se limite à des totaux par commune,
sans contour de la zone touchée. Les alternatives européennes existantes ne détectent
généralement pas les feux en dessous d'une trentaine d'hectares et mélangent incendies et
brûlages agricoles.

Résultat : pendant toute une saison, journalistes, élus, chercheurs et assureurs travaillent
sans chiffres à jour. Brûlis comble ce trou.

## Comment ça marche

Deux satellites, deux temporalités.

**1. Les points chauds (~3 h de délai).** Les satellites VIIRS de la NASA repèrent les
anomalies thermiques à la surface du globe. C'est rapide, mais grossier : un point chaud est un
carré de 375 m de côté anormalement chaud. Il signale qu'il se passe quelque chose, pas
l'étendue du sinistre — et une torchère industrielle en déclenche autant qu'un feu de forêt.

**2. Les contours (1 à 3 jours de délai).** Là où quelque chose a chauffé, on compare deux
images du satellite européen Sentinel-2 : une avant, une après. La végétation brûlée réfléchit
la lumière autrement que la végétation saine ; cette différence, mesurée pixel par pixel,
dessine le contour de la zone brûlée et permet d'en calculer la surface.

Ce délai n'est pas un choix éditorial, c'est une contrainte physique : un satellite optique ne
voit le sol qu'à son prochain passage, et seulement si les nuages le permettent. Chaque
résultat publié porte donc sa date, son délai et ses réserves.

## Les données

Chaque incendie détecté est archivé dans `data/feux/` avec son contour
(`perimetre.geojson`) et sa fiche (`info.json`) :

| Champ | Signification |
|---|---|
| `feu`, `commune`, `departement` | Localisation |
| `surface_ha` | Surface estimée, en hectares |
| `premier_point_chaud`, `dernier_point_chaud` | Période d'activité détectée |
| `image_avant`, `image_apres` | Images satellite utilisées, avec leur couverture nuageuse |
| `latence_jours` | Délai entre le feu et la mesure |
| `calcule_le` | Date du calcul |

Les fichiers sont mis à jour automatiquement deux fois par jour, et l'archive est versionnée :
on peut retrouver l'état des données à n'importe quelle date passée.

## Ce que Brûlis ne voit pas

Afficher les limites fait partie du projet. Comparée aux contours officiels Copernicus EMS sur
quatre incendies de 2022, la méthode retrouve entre 75 % et 92 % de la surface réelle
(protocole et chiffres détaillés dans `docs/phase0-resultats.md`).

Concrètement :

- **Les petits feux échappent à la détection.** En dessous d'environ un hectare, le signal est
  trop faible pour être distingué du bruit.
- **Les nuages créent des trous.** Une zone couverte au moment du passage du satellite ne sera
  mesurée qu'au passage suivant.
- **Les surfaces sont des estimations**, pas des mesures cadastrales : des ordres de grandeur.
- **Des confusions restent possibles**, notamment avec des coupes forestières récentes, qui
  modifient la végétation d'une façon comparable.

## Réutiliser les données

Les contours et les surfaces sont publiés sous **Licence Ouverte 2.0**. Vous pouvez les
réutiliser librement, y compris à des fins commerciales, à la seule condition de citer
l'origine :

> Périmètres de zones brûlées — Brûlis, d'après Copernicus Sentinel-2 et NASA FIRMS,
> version du JJ/MM/AAAA.

Le code est sous licence **MIT**. Détail des licences et des sources amont dans
`LICENCE-DONNEES.md`.

## Faire tourner le projet

La chaîne complète est automatisée par le workflow `.github/workflows/mise-a-jour.yml`, relancé
deux fois par jour : points chauds, recherche d'images, calcul des contours, contrôle qualité,
publication.

Pour une exécution manuelle, voir `SETUP.md` (environnement et clés d'accès aux données), puis :

```sh
make aide
```

## Sources

Brûlis s'appuie exclusivement sur des données publiques : **Copernicus Sentinel-2** (Union
européenne), **NASA FIRMS** pour les points chauds VIIRS, et l'**IGN** pour les fonds de carte
et le référentiel des communes.
