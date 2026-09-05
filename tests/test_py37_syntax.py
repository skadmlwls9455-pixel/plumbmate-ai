"""Game-side sources must stay parseable by the game's embedded Python 3.7.

The build compiles with the game's real interpreter, so a 3.8+ construct
(walrus, f-string ``=``, positional-only markers) would fail there — but only
on a machine with the game installed. This guard fails fast everywhere,
including CI, which matters in a codebase edited by many hands.
"""

import ast
import unittest
from pathlib import Path

GAME_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "game_mod/src/plumbmate_mod"


class Py37SyntaxTests(unittest.TestCase):
    def test_game_side_sources_parse_as_python_37(self):
        sources = sorted(GAME_SOURCE_ROOT.glob("*.py"))
        self.assertTrue(sources, "no game-side sources found")
        for path in sources:
            with self.subTest(source=path.name):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 7),
                )


if __name__ == "__main__":
    unittest.main()
