# ECOM — démo boutique

Boutique de démonstration (thème Shopify adapté), en français.

## Lancer en local

```bash
python3 server.py
```

Puis ouvrir http://localhost:8000

## Déploiement

Le fichier `render.yaml` contient toute la configuration.
Sur Render : **New +** → **Blueprint** → choisir ce dépôt → **Deploy**.

Aucune dépendance à installer : le projet n'utilise que la bibliothèque
standard de Python.
