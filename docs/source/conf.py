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
    "sphinxcontrib.video",
    # Markdown source alongside reST, so repo-root documents (CONTEXT.md and
    # the like) can be ``include``d rather than copied into a ``.rst``.
    "myst_parser",
    # Diagrams as text in the repo -- diffable and reviewable in a PR.  Every
    # one of the 60 figures under docs/ today is a PNG with no committed
    # source.
    "sphinxcontrib.mermaid",
    # Already in the `docs` dependency group but never loaded, so it was a
    # dependency being paid for and not used.  Per-page "last updated" stamps
    # are a cheap partial mitigation for docs staleness.
    "sphinx_last_updated_by_git",
]

extlinks = {
    "issue": ("https://github.com/iossifovlab/gain/issues/%s", "#%s"),
}

# ``sphinx_last_updated_by_git`` dates each page from its last Git commit, and
# a page Git has never seen loses its "View page source" link by default
# (``git_untracked_show_sourcelink`` is False).  The whole ``sphinx-apidoc``
# tree under ``development/gain/`` is exactly that case: ``build_docs.sh``
# deletes and regenerates it on every build and none of it is committed.  Left
# at the default, enabling the extension would silently strip a sourcelink
# those pages carry today -- a change this issue did not ask for.  Keep them.
#
# The stamps themselves do survive on that tree, which is worth knowing before
# anyone "fixes" it: ``git_untracked_check_dependencies`` defaults to True, so
# an untracked page is dated from its dependencies instead, and an
# ``automodule`` page depends on the ``.py`` files it documents.  21 of the 22
# generated pages are therefore stamped with the last commit of the module
# they render.
git_untracked_show_sourcelink = True

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
