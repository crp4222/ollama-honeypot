# Vérification du 11 septembre 2026 — Ollama existant

Les vérifications ont été réalisées en **mode `local`**, avec une écoute sur
une adresse LAN précise de l'hôte, au port **11435**.
Le laboratoire passait par Ollama 0.33.3 installé sur macOS, via le relais privé Docker
et `host.docker.internal:11434`. L'Ollama existant gère l'authentification cloud.
Aucune clé supplémentaire ni copie de ses identifiants n'a été nécessaire.

## Inférences réelles validées

- `/api/chat` : Kimi K3 a répondu `LOCAL_OK`, HTTP 200. La requête demandait un
  autre modèle et comportait des instructions système clientes : la capture
  confirme leur remplacement par `kimi-k3:cloud` et le prompt serveur.
- `/v1/messages` : Kimi K3 a répondu `STREAM_OK` en streaming Anthropic, HTTP 200.
  La réponse reçue est identique, octet pour octet, à la capture reconstituée.
- Ces deux appels courts ont utilisé le compte cloud de l'Ollama existant.

## Tests et cloisonnement

- 37 tests Python réussis, avec 31 sous-cas supplémentaires : modèle et système
  imposés, absence de clé nécessaire en mode local, routes refusées, captures,
  streaming, quotas persistants, concurrence, déconnexions et limites de taille.
- Seul `edge` publie le port 11435 sur l'adresse LAN choisie. Aucun port hôte publié par `gateway`
  ou `relay`. Conteneurs non root, racines en lecture seule, capabilities supprimées.
- Connexion directe de `gateway` vers `1.1.1.1:443` : bloquée.
- Relais local : requête vide refusée par Ollama sans inférence ; route `/api/pull`
  bloquée par le relais.
- Lecture des captures privées avec `scripts/watch.py --docker` : validée.
- Visualiseur : 11 tests couvrent les morceaux UTF-8/JSON/SSE, les trois formats
  d'API, le thinking regroupé, les arguments d'outils fragmentés, leurs résultats,
  les requêtes concurrentes, l'historique masqué et l'affichage sûr dans le terminal.
  Un échange réel a été relu avec le nouvel affichage, sans nouvel appel modèle.

## Quotas désactivés pour les tests OpenCode

Le mode illimité a été vérifié avec `MAX_REQUESTS_PER_DAY=0` et
`MAX_REQUESTS_TOTAL=0`. Les compteurs existants sont conservés. La passerelle accepte `0`
pour désactiver chaque quota séparément et refuse les valeurs négatives.
Les tests couvrent le passage d'un compteur épuisé au mode illimité après
redémarrage, ainsi que le maintien de l'autre plafond si un seul est désactivé.

Après reconstruction et redémarrage, les deux quotas à `0` sont confirmés dans
le journal de démarrage. Le prompt actif correspond toujours au fichier hôte.
Un appel réel court à `/v1/chat/completions` a réussi avec HTTP 200 malgré le
compteur précédemment épuisé, sans erreur de quota.

## Rechargement du prompt système

Le montage du fichier seul était devenu illisible après sa modification, tandis
que la passerelle continuait d'utiliser le prompt initial chargé en mémoire.
Le montage porte désormais sur le dossier `config` en lecture seule. La passerelle
a été recréée et l'entrée locale redémarrée ; les empreintes du fichier hôte,
du fichier dans le conteneur et du prompt chargé au démarrage sont identiques.
Un remplacement atomique de fichier temporaire est bien visible dans le conteneur,
sans modifier le prompt de l'utilisateur. L'endpoint `/api/tags` répond HTTP 200
sur le port LAN choisi et n'annonce que `kimi-k3:cloud`. Les restrictions des
conteneurs et la publication sur cette seule adresse LAN sont conservées.
Ces vérifications n'ont déclenché aucune nouvelle inférence cloud.

Après chaque modification du prompt, `docker compose restart gateway` recharge
le fichier. Tester dans une nouvelle conversation évite de renvoyer les anciennes
réponses du modèle dans l'historique.

Le modèle cloud a été enregistré dans l'Ollama existant. Les protections du
laboratoire s'appliquent à son entrée 11435, pas aux accès directs à Ollama sur
11434 ni aux autres applications qui l'utilisent. Son adresse d'écoute doit
être vérifiée séparément.

Le réseau de la box, ses redirections et son pare-feu n'ont pas été modifiés.
La connectivité depuis un autre appareil physique du LAN n'a pas été testée.
Une session complète du logiciel Claude Code reste à vérifier ; son format API
Anthropic en streaming a bien été testé avec la vraie inférence.
Le prompt de `config/system.txt` reste modifiable par le propriétaire.
