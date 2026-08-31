"""A mangled regex escape is invisible: it compiles, and it never matches.

`\\b` written into a non-raw Python string becomes a BACKSPACE character.
The regex still compiles, the module still imports, the tests around it
still pass, and the pattern silently matches nothing. That happened here:
the ASX pre-tax rule was written through a patch script that ate the
backslashes, so it returned None on a document it had been written against
and the pipeline fell through to the old, wrong answer. It was only caught
by dumping the bytes.

The whole class is cheap to exclude, so it is excluded here rather than
looked for again.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]

# control characters a mangled escape leaves behind, and the escape that
# produces each. \n, \r and \t are excluded: they are legitimate in source.
MANGLED = {"\x07": r"\a", "\x08": r"\b", "\x0b": r"\v", "\x0c": r"\f",
           "\x00": r"\0", "\x1b": r"\e", "\x7f": "DEL"}


def _sources():
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in (".venv", ".git", "build", "dist") for part in p.parts):
            continue
        yield p


def test_no_source_file_contains_a_mangled_regex_escape():
    bad = []
    for p in _sources():
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for ch, esc in MANGLED.items():
                if ch in line:
                    bad.append(f"{p.relative_to(ROOT)}:{lineno} contains the "
                               f"character {esc!r} would produce: {line.strip()[:90]!r}")
    assert not bad, ("a regex escape was written into a non-raw string and "
                     "became a control character:\n  " + "\n  ".join(bad))


def test_every_compiled_pattern_in_the_shipped_modules_is_clean():
    """The same check at runtime, over the patterns actually in use."""
    import importlib
    import pkgutil

    bad = []
    for pkg in ("cef_live", "au_lic", "uk_cef"):
        try:
            mod = importlib.import_module(pkg)
        except Exception:
            continue
        for info in pkgutil.walk_packages(mod.__path__, prefix=f"{pkg}."):
            try:
                m = importlib.import_module(info.name)
            except Exception:
                continue          # optional deps are not this test's business
            for name, obj in vars(m).items():
                if isinstance(obj, re.Pattern) and isinstance(obj.pattern, str):
                    for ch in MANGLED:
                        if ch in obj.pattern:
                            bad.append(f"{info.name}.{name}")
    assert not bad, f"compiled patterns contain control characters: {bad}"


@pytest.mark.parametrize("module,name,should_match,should_not", [
    ("cef_live.harvest_nav", "ASX_PRE_TAX", ["pre-tax", "pre tax", "before tax"],
     ["post tax", "prefix taxonomy"]),
    ("cef_live.harvest_nav", "ASX_POST_TAX", ["post-tax", "post tax", "after tax"],
     ["pre-tax"]),
    ("cef_live.harvest_nav", "ASX_PER_SHARE_LABEL",
     ["Net Tangible Asset per share", "NTA per share", "net tangible assets per share"],
     ["NTA per unit", "portfolio per share of holdings"]),
    ("cef_live.harvest_nav", "AU_NAV_HEAD",
     ["Net Tangible Asset Backing", "UWC Investment Portfolio Performance July 2026",
      "Daily Fund Update", "Weekly NTA Update"],
     ["Update - Notification of buy-back - AFI", "Appendix 4G"]),
    ("cef_live.harvest_nav", "UK_ZDP", ["zero dividend", "preference share"], ["ordinary share"]),
    ("cef_live.harvest_nav", "UK_DIVIDEND", ["dividend", "distribution"], ["divided", "net asset value"]),
    ("cef_live.harvest_nav", "UK_FOREIGN", ["cents", "US $", "EUR"], ["percent", "recent"]),
])
def test_the_critical_patterns_match_what_they_claim(module, name, should_match, should_not):
    """A pattern can be un-mangled and still wrong. These are the ones whose
    silence would change a NAV, so they are checked behaviourally."""
    import importlib
    pat = getattr(importlib.import_module(module), name)
    for s in should_match:
        assert pat.search(s), f"{name} failed to match {s!r}"
    for s in should_not:
        assert not pat.search(s), f"{name} wrongly matched {s!r}"
