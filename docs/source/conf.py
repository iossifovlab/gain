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
    # NOT ``sphinx.ext.autosectionlabel`` -- see the note below the list.
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

# Why ``sphinx.ext.autosectionlabel`` is absent (gain#1183).  It promotes every
# section title to a cross-reference label.  Numpy-style docstring headings
# become sections on every page ``sphinx-apidoc`` generates, and apidoc's own
# skeleton repeats "Submodules" / "Module contents" per package, so the same
# handful of labels was defined hundreds of times -- 96 of the build's
# warnings.  Nothing referenced them: no ``:ref:`` role under docs/source or in
# a core/gain docstring, no myst ``](#anchor)`` link, and no ``intersphinx`` in
# gain or gpf, so no other project could reach gain's inventory either.
#
# Neither knob rescues it, measured on the pre-fix tree: the 93 duplicates in
# the apidoc subtree are 48 from docstring headings and 45 from apidoc's own
# skeleton, and both ``autosectionlabel_maxdepth = 2`` and
# ``autosectionlabel_prefix_document = True`` still leave those 45.  Re-run the
# census before adding the extension back for any reason.

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

# ``development/architecture_overview.rst`` includes a slice of the repo-root
# ``CONTEXT.md`` through the myst parser, and that slice starts at a ``###``
# heading -- it is a fragment of a document, nested under the page's own
# sections.  myst-parser sees a document whose first heading is not H1 and
# warns "Document headings start at H3, not H1" once per ``###`` in the slice.
# The headings nest correctly in the rendered page, so the warning is noise
# for this use of ``include``; silence that one check (gain#1142).
suppress_warnings = ["myst.header"]

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

# No ``html_static_path``.  The setting resolves relative to THIS file's
# directory, so ``['_static']`` meant ``docs/source/_static`` -- but the
# directory the gpf_documentation import (b5656c83e) actually carried was
# ``docs/_static``, one level too high.  Sphinx warned on every build and no
# asset was ever served from either place.  Removed along with the stray
# ``docs/_static/.keep``, which existed only to keep that empty directory in
# git.  Whoever adds the first real static asset should add the setting back
# beside it -- and put the directory next to this file (gain#1183).


html_theme_options = {
    "collapse_navigation": False,   # don’t hide other sections
    "navigation_depth": 4,          # how deep the sidebar expands
    "titles_only": False,           # show headings, not just titles
    "sticky_navigation": True,
    "includehidden": True,          # include pages even if hidden
}
