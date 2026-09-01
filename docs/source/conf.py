# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'GAIn Documentation'
copyright = '2018-2025, iossifovlab.com'
author = 'iossifovlab.com'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx.ext.extlinks",
    "sphinx.ext.imgmath",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosectionlabel",
    "sphinx_copybutton",
    "sphinxcontrib.httpdomain",
    "sphinx_autorun",
    "sphinxcontrib.video"
]

extlinks = {
    "issue": ("https://github.com/iossifovlab/gain/issues/%s", "#%s"),
}

# ``sphinx-apidoc`` emits one ``automodule`` per submodule plus a "Module
# contents" one for the package itself.  Where a package ``__init__``
# re-exports its submodules' names through ``__all__``, autodoc honours
# ``__all__`` and documents every one of them a SECOND time, under the
# package.  Two anchors for one object make every short cross-reference to it
# ambiguous -- "more than one target found" -- and Sphinx then links whichever
# it saw first.  Ignoring ``__all__`` restores the ``__module__`` check, so a
# re-export is documented once, at the module that defines it, on the same
# page (gain#1033).
#
# Scope: this applies to every module that declares ``__all__`` -- eight in
# gain today, not only the package ``__init__``s -- and to nothing else; a
# module without ``__all__`` was already documented this way.  Two things it
# costs, both measured by diffing the rendered anchor sets across a build:
#
# * A facade-form anchor disappears, so a deep link ending
#   ``genomic_scores.html#gain.genomic_resources.genomic_scores.GenomicScore``
#   no longer jumps.  The page is unchanged and still carries the object at
#   its defining-module anchor, ``...genomic_scores.base.GenomicScore``.
# * Nine undocumented *instance* attributes of re-exported classes on the
#   ``genomic_position_table`` page (``TabixGenomicPositionTable.pysam_file``
#   and the like) lose their entry outright: autodoc renders those under the
#   facade but not under the module that defines them.  Each rendered as a
#   bare ``name: type`` stub with an empty body, so no prose is lost.
#
# Against that, 194 duplicate anchors go away and the three
# ``more than one target found`` classes (``GenomicScore``, ``builders``,
# ``GeneModels``) are resolved.
autodoc_default_options = {
    "ignore-module-all": True,
}

templates_path = ['_templates']
exclude_patterns = [
    '_build', 'Thumbs.db', '.DS_Store',
]



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']


html_theme_options = {
    "collapse_navigation": False,   # don’t hide other sections
    "navigation_depth": 4,          # how deep the sidebar expands
    "titles_only": False,           # show headings, not just titles
    "sticky_navigation": True,
    "includehidden": True,          # include pages even if hidden
}
