"""Sphinx configuration for diskripr documentation."""
import os
import sys

# Make the installed package importable for autodoc.
sys.path.insert(0, os.path.abspath("../src"))

project = "diskripr"
author = "diskripr contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_click",
    "myst_parser",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# Treat .md files as MyST Markdown so CHANGELOG.md can be included.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

html_theme = "sphinx_rtd_theme"
nitpicky = True

# ---------------------------------------------------------------------------
# Nitpick exceptions
# ---------------------------------------------------------------------------
# Suppress reference warnings that cannot be resolved due to structural
# limitations of autodoc rather than actual broken links.

nitpick_ignore = [
    # autodoc expands TypeAlias Literals (RipMode, EncodeFormat) into their
    # full repr, which Sphinx then tries to cross-reference as a py:obj target.
    # The truncated strings below are what Sphinx generates — not resolvable.
    ("py:obj", "typing.Literal['main'"),
    ("py:obj", "typing.Literal['h264'"),
    # Pipeline instance attributes are assigned in __init__ with type
    # annotations but are not registered as :attr: targets by autodoc.
    # The docstring references are correct; Sphinx just can't resolve them.
    ("py:attr", "Pipeline.selection"),
    ("py:attr", "Pipeline.disc_info"),
    ("py:attr", "Pipeline.rip_results"),
    ("py:attr", "Pipeline.encode_results"),
    ("py:attr", "Pipeline.output_paths"),
]

# autodoc defaults: show members and undoc'd members; preserve source order.
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

autodoc_member_order = "bysource"
