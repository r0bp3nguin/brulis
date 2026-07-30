# Brûlis — pilotage du projet.
#     make            aide
#     make site       reconstruit le site depuis les données déjà calculées
#     make detecter   cherche les feux en cours et calcule leurs périmètres
#     make verifier   contrôle qu'aucun secret ne partirait en ligne
#     make deploy     publie sur Cloudflare Pages

PY      := uv run python
SCRIPTS := PYTHONPATH=scripts $(PY)
# Attention : un commentaire en fin de ligne inclut les espaces qui le précèdent dans
# la valeur. Les commentaires sont donc placés au-dessus.
# Nom du projet Cloudflare Pages
PROJET  := brulis
# Fenêtre FIRMS pour la détection (jours)
JOURS   := 5
# Foyers traités par passage
MAX     := 10
# Points chauds minimum pour retenir un foyer
MIN_PTS := 5

.DEFAULT_GOAL := aide
.PHONY: aide install reference dnbr detecter site consolider apercu qgis visionneuse \
        verifier servir deploy propre tout

aide:
	@echo "Brûlis — périmètres de zones brûlées en open data"
	@echo
	@echo "  make install      environnement Python (uv sync)"
	@echo "  make detecter     feux en cours : FIRMS -> Sentinel-2 -> périmètres"
	@echo "  make site         (re)construit site/ à partir des données calculées"
	@echo "  make servir       sert site/ en local sur http://localhost:8000"
	@echo "  make verifier     audit : aucun secret ne doit partir en ligne"
	@echo "  make deploy       publie sur Cloudflare Pages (lance verifier d'abord)"
	@echo
	@echo "  make reference    (re)télécharge les périmètres officiels EMS 2022"
	@echo "  make dnbr         recalcule les 4 cas de référence girondins"
	@echo "  make apercu       planches PNG de vérification"
	@echo "  make visionneuse  visionneuse HTML autonome"
	@echo "  make qgis         projet QGIS pré-stylé"
	@echo "  make consolider   vérifie qu'un incendie n'a bien qu'une fiche"
	@echo "  make propre       efface les sorties régénérables"
	@echo
	@echo "Variables : JOURS=$(JOURS) MAX=$(MAX) MIN_PTS=$(MIN_PTS) PROJET=$(PROJET)"

install:
	uv sync

# --- données de référence (feux girondins 2022, vérité Copernicus EMS) ---------

reference:
	$(SCRIPTS) scripts/fetch_ems.py
	$(SCRIPTS) scripts/ems_context.py
	$(SCRIPTS) scripts/build_reference.py

data/reference/perimetres_ems_2022.geojson:
	$(MAKE) reference

dnbr: data/reference/perimetres_ems_2022.geojson
	$(SCRIPTS) scripts/dnbr.py --produit EMSR592_AOI01_DEL_PRODUCT_r1_RTP01_v1 \
	    --pre S2A_30TXQ_20220712_0_L2A --post S2B_30TXQ_20220717_0_L2A
	$(SCRIPTS) scripts/dnbr.py --produit EMSR592_AOI02_DEL_PRODUCT_r1_RTP01_v1 \
	    --pre S2A_30TXQ_20220712_0_L2A --post S2B_30TXQ_20220717_0_L2A
	$(SCRIPTS) scripts/dnbr.py --produit EMSR633_AOI01_DEL_MONIT01_r1_VECTORS_v1 \
	    --pre S2B_30TXQ_20220905_0_L2A --post S2A_30TXQ_20220920_0_L2A
	$(SCRIPTS) scripts/dnbr.py --produit EMSR619_AOI01_DEL_MONIT01_r1_RTP01_v1 \
	    --pre S2B_30TXQ_20220806_0_L2A --post S2A_30TXQ_20220811_0_L2A \
	    --exclure-produit EMSR592_AOI01_GRA_PRODUCT_r1_RTP01_v1 --seuil-retenu 0.10

# --- production ---------------------------------------------------------------

foyers:
	$(SCRIPTS) scripts/firms.py --jours $(JOURS)

detecter:
	$(SCRIPTS) scripts/detecter.py --jours $(JOURS) --max-foyers $(MAX) \
	    --min-points $(MIN_PTS)

site:
	$(SCRIPTS) scripts/site.py

# Entretien de l'archive. `detecter` garde désormais une fiche par incendie, mais celles
# qu'il a laissées en double avant cette règle se rattrapent ici.
consolider:
	$(SCRIPTS) scripts/consolider.py --verifier

servir: site
	@echo "http://localhost:8000  (Ctrl-C pour arrêter)"
	@cd site && $(PY) -m http.server 8000

# --- vérification --------------------------------------------------------------

apercu:
	$(SCRIPTS) scripts/apercu.py

visionneuse:
	$(SCRIPTS) scripts/visionneuse.py

qgis:
	$(SCRIPTS) scripts/projet_qgis.py

verifier:
	@$(SCRIPTS) scripts/verifier_publication.py

# --- déploiement ---------------------------------------------------------------

deploy: site verifier
	@command -v npx >/dev/null || { echo "npx introuvable — installer Node.js"; exit 1; }
	npx --yes wrangler@latest pages deploy site --project-name $(PROJET)

propre:
	rm -rf site/index.html site/data data/work
	@echo "sorties effacées — data/reference et data/feux conservés"

tout: reference dnbr site verifier
