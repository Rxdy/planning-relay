.PHONY: release

release: ## Publie une version : make release V=1.0.0 (tag vX.Y.Z sur main, déjà en prod ; la CI pose la version sur les images et crée la release)
	@.github/images/release.sh "$(V)"
