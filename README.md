# 🔀 PR Dashboard — MVP

Dashboard Streamlit simplifié pour suivre les PR GitHub & Azure DevOps.

## Lancer

```bash
pip install streamlit requests
streamlit run app.py
```

Le mode démo s'active par défaut — pas besoin de tokens pour tester.

## Config

Dans la sidebar :
- **Mode démo** : données fictives pour tester l'interface
- **Membres** : liste des initiales séparées par virgule (ex: `CRI, PCH, JHE`)
- **GitHub** : token + repos (un par ligne, format `owner/repo`)
- **Azure DevOps** : token + organisation + repos (un par ligne, format `project/repo`)

## Fonctionnement

- Un tableau dépliable par repository
- Colonnes : Auteur, Titre, Date création, Date modif, Statut review, Commentaires, Lien
- Une colonne par membre d'équipe avec sélecteur emoji (👀 ✅ ⚠️ 🔧 ❌ 🚀)
- Les marques sont sauvegardées localement dans `user_marks.json`
