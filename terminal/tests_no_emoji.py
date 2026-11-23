"""Test automatique garantissant l'absence d'emojis et de symboles pictographiques."""

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


# Plages Unicode d'emojis, dingbats, symboles pictographiques et variantes courantes
EMOJI_AND_PICTOGRAPH_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # Symboles et pictogrammes divers
    "\U0001F600-\U0001F64F"  # Émoticônes
    "\U0001F680-\U0001F6FF"  # Transport et cartes
    "\U0001F700-\U0001F77F"  # Symboles alchimiques
    "\U0001F780-\U0001F7FF"  # Formes géométriques étendues
    "\U0001F800-\U0001F8FF"  # Flèches supplémentaires-C
    "\U0001F900-\U0001F9FF"  # Symboles et pictogrammes supplémentaires
    "\U0001FA00-\U0001FAFF"  # Symboles et pictogrammes étendus-A
    "\u2600-\u26FF"          # Symboles divers (⚠, ★, etc.)
    "\u2700-\u27BF"          # Casseau / Dingbats (✔, ✖, ➜, ✅, ❌, etc.)
    "\u2B50\u2B55\u23E9-\u23FA\u25AA-\u25FE"
    "\uFE0F"                 # Sélecteur de variante emoji
    "]"
)


class NoEmojiOrPictographTests(SimpleTestCase):
    """Vérifie qu'aucun emoji ni symbole pictographique n'existe dans les templates, JS ou code."""

    def test_no_emoji_or_pictographs_in_templates_js_and_apps(self):
        base_dir = Path(settings.BASE_DIR)
        targets = [
            (base_dir / 'templates', {'.html', '.js', '.txt'}),
            (base_dir / 'static' / 'js', {'.js'}),
            (base_dir / 'terminal', {'.py'}),
            (base_dir / 'accounts', {'.py'}),
        ]

        offenses = []
        self_path = Path(__file__).resolve()

        for root, extensions in targets:
            if not root.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if '__pycache__' in dirpath:
                    continue
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    if fpath.suffix.lower() not in extensions:
                        continue
                    if fpath.resolve() == self_path:
                        continue
                    text = fpath.read_text(encoding='utf-8', errors='replace')
                    for lineno, line in enumerate(text.splitlines(), start=1):
                        match = EMOJI_AND_PICTOGRAPH_RE.search(line)
                        if match:
                            offenses.append(
                                f"{fpath.relative_to(base_dir)}:{lineno} -> {repr(match.group(0))}"
                            )

        self.assertEqual(
            offenses,
            [],
            "Emojis ou symboles pictographiques détectés :\n" + "\n".join(offenses),
        )
