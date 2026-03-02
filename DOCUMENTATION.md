# PolySniper v2.0 — Documentation Complète

## Vue d'ensemble

PolySniper est un bot de trading automatisé pour [Polymarket](https://polymarket.com).
Il scanne **tous les marchés actifs** en continu et achète des tokens dont le prix est
entre **0.95 et 0.99** (probabilité quasi-certaine), sur des marchés qui se terminent
bientôt. L'objectif : capturer des micro-profits sur des résolutions quasi-certaines.

---

## Architecture

```
main.py
  ├── MarketScanner       (scanne les marchés via Gamma API)
  │     ↓ Opportunity
  ├── SniperEngine        (vérifie CLOB, sizing, risk, exécute les trades)
  │     ├── RiskManager   (limites de positions, dedup, PnL)
  │     ├── PositionClaimer (auto-claim on-chain après résolution)
  │     └── OrderSizer    (calcul de la taille des ordres)
  ├── CircuitBreaker      (halt automatique si le scanner échoue)
  ├── StateStore          (persistance état → data/state.json)
  ├── Dashboard           (interface web → port 8080)
  └── Telegram Alerts     (notifications en temps réel)
```

---

## Flux de fonctionnement

### 1. Scan des marchés (`core/scanner.py`)

Toutes les **N secondes** (`SCANNER_INTERVAL`), le scanner :

1. Appelle la **Gamma API** (`gamma-api.polymarket.com/markets`) avec pagination
2. Récupère **tous les marchés actifs** (~33 000+)
3. Pré-filtre par :
   - **Volume minimum** (`MIN_VOLUME_USDC`) — ex: $50K+
   - **Date de fin** (`MAX_END_HOURS`) — le marché doit se terminer dans les X heures
   - **Date dans la question** — extraction regex de dates dans le titre du marché
   - **Prix indicatif** — au moins un token entre `MIN_BUY_PRICE` et `MAX_BUY_PRICE`
4. Vérifie le **cooldown** (`_seen` TTL = 60s) — un token déjà envoyé récemment n'est pas renvoyé
5. Envoie les `Opportunity` dans une queue async vers l'engine

### 2. Exécution des trades (`core/engine.py`)

Pour chaque opportunité reçue :

1. **Vérification CLOB** — appelle `best_ask()` pour obtenir le vrai prix sur l'order book
2. **Validation du prix** — le prix CLOB doit être dans la fenêtre [0.95–0.99]
3. **Sizing dynamique** :
   - Récupère le solde USDC.e via `get_balance_usdc()`
   - Calcule : `montant = solde / slots_disponibles`
   - Minimum $5 par trade
4. **Risk check** — le RiskManager vérifie :
   - Nombre de positions ouvertes < `MAX_OPEN_POSITIONS`
   - Pas de doublon (même token ou même marché déjà en position)
   - Exposition totale sous le plafond
   - Cooldown entre marchés respecté
5. **Exécution** — `market_buy()` sur le CLOB (prix = $0.999)
6. **Enregistrement** — position ajoutée au RiskManager + sauvegarde état
7. **Alerte Telegram** — notification avec détails du trade

### 3. Monitoring des positions (`core/engine.py`)

Toutes les **30 secondes**, le monitor vérifie chaque position ouverte :

1. **Résolution API** — appelle `get_market_resolution()` pour vérifier si le marché
   est officiellement résolu (`resolved: true`)
   - Compare le résultat gagnant avec le token acheté (Yes/No/Up/Down)
   - Si résolu : calcule le PnL et ferme la position
2. **Détection ghost** — si l'order book retourne une erreur 404 pendant
   60 cycles consécutifs (~30 min), la position est considérée fantôme et supprimée
3. **Stop-loss** — si configuré, vend la position si le prix chute sous le seuil

### 4. Auto-claim (`core/claimer.py`)

Quand une position gagnante est résolue :

1. Le `PositionClaimer` construit une transaction `redeemPositions` sur le
   **Conditional Token Framework** (CTF)
2. La transaction est exécutée via le **Gnosis Safe** (proxy wallet) :
   - Calcul du hash de transaction Safe
   - Signature avec la clé privée EOA
   - Envoi de `execTransaction` sur la blockchain Polygon
3. Après le claim, `refresh_balance()` force le CLOB à resync son cache interne
4. Les USDC.e reviennent dans le proxy wallet, prêts pour de nouveaux trades

### 5. Dashboard web (`core/dashboard.py`)

Accessible sur `http://<IP>:8080` :

- **Résumé** : positions ouvertes, PnL session, PnL total réalisé, wins/losses
- **Positions ouvertes** : détail de chaque position avec prix d'achat
- **Positions fermées** : historique avec PnL par position
- **Trades récents** : log des dernières exécutions
- **Stats scanner** : marchés scannés, candidats, opportunités
- Auto-refresh toutes les 5 secondes
- API JSON : `GET /api/status`

---

## Fichiers du projet

### Modules principaux (`core/`)

| Fichier | Rôle |
|---------|------|
| `config.py` | Chargement .env, validation de tous les paramètres |
| `scanner.py` | Scan Gamma API, pré-filtrage, extraction de dates |
| `engine.py` | Moteur de trading, monitoring positions, sizing dynamique |
| `polymarket.py` | Client CLOB async (orders, order book, balance, resolution) |
| `risk.py` | RiskManager (limites, dedup, PnL, positions ouvertes/fermées) |
| `claimer.py` | Auto-claim on-chain via Gnosis Safe |
| `persistence.py` | Sauvegarde/restauration état (data/state.json) |
| `dashboard.py` | Serveur web dashboard |
| `circuit_breaker.py` | Halt automatique si le scanner échoue |
| `rate_limiter.py` | Throttling des appels API |
| `sizing.py` | Calcul taille des ordres (fixed/confidence/kelly) |

### Scripts utilitaires (racine)

| Script | Usage |
|--------|-------|
| `derive_keys.py` | Génère les credentials API CLOB à partir de la clé privée |
| `setup_allowance.py` | Force le CLOB à resync la balance on-chain |
| `claim.py` | Claim manuel : `python3 claim.py <condition_id>` ou `--all` |
| `approve_usdc.py` | Approve on-chain USDC.e et Conditional Tokens |
| `check_wallet.py` | Vérifie solde POL et USDC.e on-chain |
| `debug_api.py` | Diagnostic complet : env, auth, order book, test order |
| `debug_wallet.py` | Trouve le bon signature_type/funder pour le CLOB |

---

## Configuration (.env)

### Paramètres essentiels

| Variable | Description | Exemple |
|----------|-------------|---------|
| `POLYMARKET_ADDRESS` | Adresse du proxy wallet Polymarket | `0xb2f...` |
| `POLY_PRIVATE_KEY` | Clé privée EOA (hex, avec 0x) | `0x13cb...` |
| `POLY_API_KEY` | Clé API CLOB (via derive_keys.py) | `YOUR_API_KEY` |
| `POLY_API_SECRET` | Secret API CLOB (base64) | `YOUR_API_SECRET` |
| `POLY_API_PASSPHRASE` | Passphrase API CLOB | `YOUR_PASSPHRASE` |
| `POLY_SIGNATURE_TYPE` | Type de signature (2 = Gnosis Safe) | `2` |
| `POLY_FUNDER` | Adresse funder (= POLYMARKET_ADDRESS) | `0xb2f...` |
| `DRY_RUN` | Mode simulation (true/false) | `false` |

### Paramètres de trading

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MIN_BUY_PRICE` | 0.95 | Prix minimum d'achat (probabilité) |
| `MAX_BUY_PRICE` | 0.99 | Prix maximum d'achat |
| `SCANNER_INTERVAL` | 30 | Intervalle de scan en secondes |
| `MIN_VOLUME_USDC` | 100000 | Volume minimum du marché ($) |
| `MAX_END_HOURS` | 6 | Le marché doit finir dans X heures |
| `MAX_OPEN_POSITIONS` | 10 | Nombre max de positions simultanées |
| `MAX_SESSION_LOSS_USDC` | 200 | Perte max par session avant halt |
| `MAX_TOTAL_EXPOSURE_USDC` | 500 | Exposition totale max ($) |
| `FEE_RATE` | 0.02 | Taux de frais (0.02 = 2%) |
| `STOP_LOSS_PCT` | 0.0 | Stop-loss (0 = désactivé) |

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Token du bot Telegram |
| `TELEGRAM_CHAT_ID` | ID du chat pour les alertes |

---

## Déploiement VPS

### Installation initiale

```bash
# Cloner le repo
git clone https://github.com/Kaelsz/Polymarket_Sniper.git
cd Polymarket_Sniper

# Créer le .env
cp .env.example .env
nano .env  # remplir les valeurs

# Générer les clés API CLOB
pip3 install python-dotenv py-clob-client
python3 derive_keys.py
# → Copier les clés dans .env

# Configurer l'allowance CLOB
python3 setup_allowance.py

# Lancer le bot
docker-compose up --build -d
```

### Commandes courantes

```bash
# Voir les logs en temps réel
docker-compose logs -f --tail=100

# Redémarrer après mise à jour
docker-compose down
git pull origin main
docker-compose up --build -d

# Voir l'état interne
docker run --rm -v polymarket_sniper_polysniper-data:/data \
  python:3.12-slim cat /data/state.json

# Forcer resync balance CLOB (après claim manuel)
python3 setup_allowance.py

# Claim manuel d'une position
python3 claim.py <condition_id>
```

### Prérequis on-chain

- **USDC.e** sur Polygon dans le proxy wallet (pour trader)
- **POL (MATIC)** dans l'EOA (pour le gas des claims on-chain)
- **Approvals** configurés (`approve_usdc.py`)
- **Localisation** : pas de VPS aux USA (Polymarket bloqué)

---

## Gestion des risques

### Protections automatiques

| Protection | Comportement |
|------------|-------------|
| **Max positions** | Bloque les nouveaux trades si le max est atteint |
| **Dedup** | Empêche d'ouvrir 2 positions sur le même token ou marché |
| **Exposition max** | Limite l'exposition totale en dollars |
| **Session loss** | Halt du bot si les pertes dépassent le seuil |
| **Circuit breaker** | Halt si le scanner échoue X fois consécutives |
| **Cooldown** | Délai entre trades sur le même marché |
| **Balance check** | Vérifie le solde avant chaque trade (min $5) |

### Cycle de vie d'une position

```
Scanner détecte opportunité (prix 0.95-0.99, volume OK, fin proche)
    ↓
Engine vérifie prix CLOB réel
    ↓
Risk manager valide (pas de doublon, slots dispo, balance OK)
    ↓
Trade exécuté → position enregistrée
    ↓
Monitor vérifie toutes les 30s si le marché est résolu
    ↓
Résolution détectée → PnL calculé
    ↓
Si WIN → auto-claim on-chain → USDC.e récupéré → balance CLOB resync
    ↓
Position fermée, slot libéré pour un nouveau trade
```

---

## Alertes Telegram

Le bot envoie des notifications pour :

- **Trade exécuté** : marché, outcome, prix, taille, latence
- **Position résolue** (WIN/LOSS) : PnL, source de résolution
- **Auto-claim** : succès ou échec du claim on-chain
- **Stop-loss déclenché** : position vendue
- **Circuit breaker** : trading suspendu/repris
- **Crash** : erreur fatale avec traceback
