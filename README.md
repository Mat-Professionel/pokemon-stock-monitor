# Moniteur de stock Pokémon 30 ans

Ce projet vérifie vos URL de produits chaque minute sur le Mac lorsqu'il est éveillé, avec GitHub Actions toutes les 30 minutes en secours. Il envoie une alerte Telegram seulement lorsqu'un produit devient réellement achetable, puis attend une rupture avant de réarmer l'alerte.

Boutiques reconnues : Smyths Toys, E.Leclerc, Fnac, Carrefour, Cultura, King Jouet, Amazon France, Cdiscount, Auchan, Micromania, JouéClub, La Grande Récré et Philibert. Une boutique inconnue utilise automatiquement le détecteur générique.

## 1. Créer le bot Telegram

1. Ouvrez Telegram et cherchez **@BotFather**.
2. Envoyez `/newbot`.
3. Choisissez un nom, puis un identifiant qui finit par `bot`.
4. BotFather fournit une valeur ressemblant à `123456:ABC...` : c'est votre `TELEGRAM_BOT_TOKEN`. Gardez-la secrète.
5. Ouvrez votre nouveau bot et appuyez sur **Démarrer**, ou envoyez-lui un message. Cette étape est indispensable.

## 2. Trouver le TELEGRAM_CHAT_ID

1. Dans un navigateur, ouvrez l'adresse suivante en remplaçant `VOTRE_TOKEN` :

   `https://api.telegram.org/botVOTRE_TOKEN/getUpdates`

2. Dans le texte affiché, cherchez `"chat":{"id":`.
3. Le nombre qui suit est votre `TELEGRAM_CHAT_ID`. Pour un groupe, il peut commencer par `-`.

Si `result` est vide, envoyez un nouveau message au bot puis rechargez la page.

## 3. Créer le dépôt GitHub

1. Sur GitHub, cliquez sur **New repository**.
2. Donnez-lui un nom, par exemple `pokemon-stock-monitor`.
3. Un dépôt public bénéficie généralement de davantage de minutes GitHub Actions. Un cron toutes les 5 minutes représente jusqu'à 288 exécutions par jour : vérifiez les quotas GitHub du compte si le dépôt est privé.
4. Copiez tout le contenu de ce dossier à la racine du dépôt. Le fichier `monitor.py` doit donc être visible directement sur la page d'accueil du dépôt.

## 4. Ajouter les deux secrets GitHub

Dans le dépôt, ouvrez **Settings → Secrets and variables → Actions → New repository secret**, puis créez exactement :

- `TELEGRAM_BOT_TOKEN` avec le token donné par BotFather ;
- `TELEGRAM_CHAT_ID` avec l'identifiant trouvé précédemment.

Les valeurs ne doivent jamais être inscrites dans `products.json`, le code ou une capture d'écran publique.

## 5. Ajouter les vraies URL produits

Ouvrez `products.json`. Pour chaque produit :

1. remplacez `PASTE_..._PRODUCT_URL_HERE` par l'URL exacte de la fiche produit ;
2. remplacez `"enabled": false` par `"enabled": true` ;
3. adaptez `name`, `store` et `max_price` ;
4. enregistrez le fichier.

Exemple :

```json
{
  "id": "smyths-etb-30",
  "name": "ETB Pokémon 30e anniversaire",
  "store": "Smyths Toys",
  "url": "https://www.smythstoys.com/fr/fr-fr/...",
  "max_price": 65,
  "enabled": true
}
```

Ne réutilisez jamais le même `id` pour deux produits. Pour ajouter un produit, dupliquez un bloc en veillant aux virgules JSON.

Options disponibles dans chaque bloc :

- `max_price`: prix maximal accepté ; supprimez la ligne pour ne pas fixer de plafond ;
- `alert_if_too_expensive`: mettez `true` pour recevoir une alerte spéciale au-dessus du plafond ;
- `require_direct_seller`: mettez `true` pour bloquer l'alerte si le vendeur officiel n'est pas identifié. Recommandé pour Fnac, Amazon, Carrefour, Cdiscount, Auchan et Leclerc ;
- `engine`: utilisez `"requests"` par défaut. Essayez `"playwright"` si la fiche reste en état inconnu parce que son contenu est chargé en JavaScript ;
- `enabled`: `false` désactive temporairement l'entrée.

### Surveiller une recherche ou une catégorie

Une entrée avec `"type": "search"` ou `"type": "category_search"` ne déclenche pas directement une alerte. Elle découvre d'abord les liens des fiches qui contiennent un EAN ou un des mots-clés configurés, puis analyse chaque fiche trouvée comme un produit normal.

```json
{
  "id": "boutique-pokemon-30-discovery",
  "name": "Recherche Pokémon 30e anniversaire",
  "store": "Nom de la boutique",
  "url": "https://boutique.example/recherche?q=pokemon",
  "eans": ["0196214144835"],
  "keywords": ["Pokémon 30e anniversaire", "ETB Pokémon 30 ans"],
  "link_patterns": ["/produit/"],
  "max_price": 65,
  "type": "search",
  "enabled": true
}
```

Le système limite par défaut la découverte à 12 fiches par source. `link_patterns` est facultatif, mais permet d'écarter les liens de menus, articles et publicités.

### Découverte par sitemap officiel

Pour E.Leclerc, Auchan, Cultura, JouéClub et La Grande Récré, le projet consulte aussi les sitemaps produits publics des enseignes. Cette voie permet de repérer une nouvelle fiche même lorsqu'elle n'apparaît pas encore dans une page de recherche interne. Les sitemaps sont relus toutes les 15 minutes, puis les fiches découvertes sont gardées dans `state.json` et contrôlées toutes les 5 minutes. Les fiches déjà connues sont toujours contrôlées en premier : un sitemap lent ne retarde donc pas leur vérification de stock.

Les champs utiles sont `type: "sitemap"`, `sitemap_include_patterns`, `max_sitemap_files`, `max_discovered_products` et `discovery_interval_minutes`. Une erreur temporaire ne vide pas le cache de la dernière découverte réussie.

Après un refus HTTP ou une panne, une source de découverte attend par défaut 60 minutes avant de réessayer (`retry_interval_minutes`). Les contrôles des fiches produit déjà connues continuent normalement entre-temps.

Important : les sites changent régulièrement leur HTML et certains bloquent les serveurs GitHub. Le programme préfère ne pas alerter lorsque le vendeur ou l'état est incertain. Consultez les logs et ajustez l'option `require_direct_seller` seulement si nécessaire.

## 6. Tester Telegram

Dans GitHub, ouvrez **Actions → Surveillance Pokémon 30 ans → Run workflow**. Le workflow normal effectuera déjà un scan.

Pour tester uniquement Telegram depuis votre ordinateur :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="votre_token"
export TELEGRAM_CHAT_ID="votre_chat_id"
python monitor.py --test-telegram
```

Vous devez recevoir : `✅ Bot Pokémon opérationnel.`

## 7. Tester l'analyse sans alerte

Après avoir installé les dépendances :

```bash
python monitor.py --test
```

Ce mode affiche les résultats, mais n'envoie aucune alerte et ne modifie pas `state.json`.

Pour un vrai scan local :

```bash
python monitor.py --once
```

## 8. Lancer et vérifier GitHub Actions

1. Ouvrez l'onglet **Actions** du dépôt et acceptez l'activation si GitHub le demande.
2. Choisissez **Surveillance Pokémon 30 ans**.
3. Cliquez sur **Run workflow** pour le premier lancement.
4. Ouvrez l'exécution et vérifiez la ligne correspondant à chaque boutique.
5. Revenez plus tard dans **Actions** : de nouvelles exécutions planifiées doivent apparaître. GitHub peut retarder un cron de quelques minutes.

Pour prouver que Telegram fonctionne sans attendre un stock, lancez manuellement le workflow et cochez **Envoyer uniquement un message de test Telegram**. Vous devez recevoir immédiatement `✅ Bot Pokémon opérationnel.`

Le planning de secours GitHub est `*/30 * * * *`. Les boutiques sont vérifiées en parallèle, avec au maximum cinq requêtes simultanées.

## Surveillance locale rapide sur le Mac

Les deux LaunchAgents fournis dans `launchd/` contrôlent les fiches connues toutes les 60 secondes et découvrent les nouvelles URL toutes les 15 minutes, indépendamment l'un de l'autre. Ils fonctionnent tant que la session macOS est ouverte et que le Mac ne dort pas. Leur copie d'exécution et leurs états sont isolés dans `~/Library/Application Support/PokemonStockMonitor/`, leurs secrets sont lus depuis le Trousseau macOS et leurs logs sont enregistrés dans `~/Library/Logs/PokemonStockMonitor/`.

Installation :

```bash
./scripts/setup_local_monitor.zsh
```

Arrêt et désinstallation réversible :

```bash
./scripts/uninstall_local_monitor.zsh
```

Le workflow GitHub reste un secours toutes les 30 minutes lorsque le Mac est éteint. Une alerte locale et une alerte GitHub peuvent exceptionnellement être envoyées pour le même retour en stock, car leurs états anti-spam sont séparés.

## Comment fonctionne l'anti-spam ?

`state.json` mémorise l'état, le dernier prix, le vendeur et la dernière alerte. Une alerte est envoyée au passage de « indisponible » à « disponible ». Elle n'est pas répétée tant que le produit reste disponible. Une rupture réarme l'alerte.

Après un changement, le workflow commite uniquement `state.json` avec `[skip ci]`. Le `GITHUB_TOKEN` intégré effectue cette sauvegarde sans secret supplémentaire. Les exécutions sont sérialisées avec `concurrency`, ce qui limite les conflits. Un état inconnu ou une erreur réseau ne réarme jamais l'alerte.

## Lire les logs

Exemples :

```text
[SMYTHS TOYS] ETB Pokémon 30e anniversaire → indisponible
[FNAC] ETB Pokémon 30e anniversaire → DISPONIBLE → 59,99 €
[CARREFOUR] ETB Pokémon 30e anniversaire → erreur: HTTP 403
```

Une erreur sur une boutique n'arrête pas les autres vérifications.

## Ajouter ou adapter une boutique

Pour une nouvelle boutique, ajoutez simplement son produit à `products.json` : le détecteur générique sera utilisé. Pour une logique dédiée, créez un petit module dans `monitors/`, ajoutez un profil dans `monitors/profiles.py`, puis associez son nom dans `monitors/__init__.py`.

Le système ne contourne ni CAPTCHA ni protection anti-bot. Il utilise des pages publiques, un navigateur standard pour le contenu JavaScript et les sitemaps officiels quand ils existent. Un site peut néanmoins refuser l'adresse IP des serveurs GitHub ; dans ce cas l'état reste « inconnu » et aucune fausse alerte n'est envoyée.

## Commandes utiles

```bash
python monitor.py --test-telegram
python monitor.py --test
python monitor.py --once
python -m unittest discover -v
```
