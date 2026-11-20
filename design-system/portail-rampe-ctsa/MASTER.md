# Système de design — Portail RAMPE (Cap Terminal S.A.)

Référence des jetons visuels, de la typographie et des composants du portail d'exploitation RAMPE et du site public de Cap Terminal S.A.

Les jetons sont centralisés dans `static/src/input.css` (`@theme`) et consommés par `static/css/ctsa.css` ainsi que les gabarits Django (`templates/`).

---

## Palette de couleurs

### Couleurs principales et de marque

| Rôle | Hex | Jeton CSS | Usage | Contraste mesuré |
|---|---|---|---|---|
| Primaire | `#1A6394` | `--color-ctsa-blue` | Boutons principaux, liens, onglets actifs, anneau de focus | 6.45:1 sur `#FFFFFF` · 6.16:1 sur `#F8FAFC` |
| Primaire (survol) | `#144D73` | `--color-ctsa-blue-dark` | Survol des boutons et liens principaux | 8.98:1 sur `#FFFFFF` |
| Vert marque CTSA | `#008751` | `--color-brand-green` | Logo CTSA, filet d'accentuation (en-tête, barre latérale, connexion) | 4.58:1 sur `#FFFFFF` |
| Vert texte (fond clair) | `#006B49` | `--color-success` | Petit texte vert sur fond clair | 6.56:1 sur `#FFFFFF` · 6.27:1 sur `#F8FAFC` |
| Compteur ambre | `#F59E0B` | `--color-pending` | Pastille de compteur (texte `#1E293B`) | 6.81:1 |

### Surfaces, chrome sombre et neutres

| Rôle | Hex | Jeton CSS | Usage |
|---|---|---|---|
| Fond applicatif | `#F8FAFC` | `--color-ctsa-surface` | Arrière-plan des pages |
| Surface carte | `#FFFFFF` | `--color-surface` | Cartes, tableaux, modales |
| Chrome sombre | `#1E293B` | `--color-chrome-dark` | Barre latérale du portail, en-tête public |
| Texte principal | `#0F172A` | `--color-text` | Titres et corps de texte (17.06:1 sur `#F8FAFC`) |
| Texte secondaire | `#475569` | `--color-text-muted` | Métadonnées, sous-titres (7.24:1 sur `#F8FAFC`) |
| Bordure de contrôle | `#64748B` | `--color-border-ui` | Champs de formulaire, contrôles interactifs (4.76:1 sur `#FFFFFF`) |
| Bordure de carte | `#E2E8F0` | `--color-border` | Séparateurs, contours de cartes et tableaux |

### Statuts opérationnels

| Statut | Texte | Fond | Contraste |
|---|---|---|---|
| Confirmé | `#065F46` | `#ECFDF5` | 7.29:1 |
| En attente | `#92400E` | `#FEF3C7` | 6.37:1 |
| Indisponible | `#991B1B` | `#FEF2F2` | 7.60:1 |

---

## Typographie

- **Interface et titres :** `Fira Sans` (graisses `400`, `500`, `600`, `700`), repli `system-ui`.
- **Codes et numéros de conteneur :** `Fira Code` (graisses `400`, `500`, `600`, `700`), repli `ui-monospace`, avec `font-variant-ligatures: none`.
- **Hébergement :** fichiers `.woff2` locaux dans `static/fonts/` avec `font-display: swap` (aucun CDN externe).

---

## Composants et ergonomie terrain

- **Boutons principaux (`.ui-btn-primary`) :** fond `#1A6394`, survol `#144D73`, texte `#FFFFFF`, cible tactile minimale `44 px` sur mobile.
- **Badges de statut (`templates/components/badge.html`) :** libellé explicite accompagné d'une icône SVG, sans jamais reposer uniquement sur la couleur.
- **Champs numéro de conteneur :** police monospace (≥ 16 px sur mobile pour éviter le zoom automatique), `autocapitalize="characters"`, `autocomplete="off"`, `spellcheck="false"`.
- **Dates :** `type="date"` ou format `jj/mm/aaaa` explicite.
- **Accessibilité et mouvement :** focus clavier visible (`focus-visible`), navigation responsive sans défilement horizontal (375 px, 768 px, 1280 px), prise en charge de `prefers-reduced-motion`.
