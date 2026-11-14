"""Fait tourner en boucle l'animation « Live » des schémas archify (docs/*.html).

Archify joue l'animation trace une seule fois au chargement, puis la fige. Pour une vitrine,
on la veut en continu. Ce script ajoute une règle CSS, sans toucher au reste de la page :
- le bouton « Still » l'arrête toujours (les règles d'arrêt d'archify sont en !important) ;
- elle respecte le réglage système « réduire les animations » ;
- elle est idempotente : relancer le script ne l'ajoute pas deux fois.

À relancer après chaque `archify deliver` :  python scripts/archify_live_loop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MARQUEUR = "<!-- live-loop -->"
STYLE = f"""{MARQUEUR}
<style>
  @media (prefers-reduced-motion: no-preference) {{
    html[data-motion-capable="true"][data-ambient-motion] svg[data-animation="trace"] [data-animate="edge"] {{
      animation: archify-edge-flow 2.4s linear infinite;
      animation-delay: calc(var(--step, 0) * 160ms);
    }}
    html[data-motion-capable="true"][data-ambient-motion] svg[data-animation="trace"] [data-animate="node"] {{
      transform-box: fill-box;
      transform-origin: center;
      animation: archify-node-pulse 3.6s ease-in-out infinite;
      animation-delay: calc(var(--step, 0) * 160ms);
    }}
  }}
</style>
"""


def patch(page: Path) -> bool:
    html = page.read_text(encoding="utf-8")
    if MARQUEUR in html or 'data-animation="trace"' not in html:
        return False
    if "</head>" not in html:
        raise ValueError(f"{page} : balise </head> introuvable")
    page.write_text(html.replace("</head>", STYLE + "</head>", 1), encoding="utf-8")
    return True


def main() -> int:
    docs = Path(__file__).resolve().parents[1] / "docs"
    for page in sorted(docs.glob("*.html")):
        if ".visual-check." in page.name:
            continue
        print(f"{page.name} : {'boucle ajoutée' if patch(page) else 'inchangé'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
