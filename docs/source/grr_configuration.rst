GRR configuration files
=======================

A GRR configuration file (also called a *GRR definition file*) is a small YAML
file that tells the GAIn command-line tools — ``grr_browse``, ``grr_manage``,
``annotate_tabular``, ``annotate_vcf``, ``annotate_variant_effects``, and the
rest — **which Genomic Resource Repositories (GRRs) to use and in what order to
search them**. It does not contain any genomic data itself; it only points to
the repositories (local directories or remote URLs) where resources live and
describes how to combine and cache them.

This page documents the structure of the configuration file, how the CLI tools
locate it, the available repository types, and resource caching. For an
introduction to GRRs and the resources they hold, see
:doc:`Genomic resources and repositories <grr>`.


How the CLI tools find the configuration
-----------------------------------------

Every CLI tool resolves the GRR it should use from the first of the following
sources that is available (highest precedence first):

1. **The** ``-g`` / ``--grr`` **command-line option** — a path to a GRR
   configuration file. This overrides everything else for that invocation:

   .. code-block:: bash

       $ grr_browse -g /path/to/my_grr_definition.yaml
       $ annotate_tabular -g /path/to/my_grr_definition.yaml input.tsv pipeline.yaml

2. **The** ``--grr-directory`` **command-line option** — a shortcut that uses a
   single local directory directly as a GRR, without writing a configuration
   file at all. It is equivalent to a one-entry configuration with a
   ``directory`` repository (see below):

   .. code-block:: bash

       $ grr_browse --grr-directory /path/to/my_grr

3. **The** ``GRR_DEFINITION_FILE`` **environment variable** — a path to a GRR
   configuration file, used when no command-line option is given:

   .. code-block:: bash

       $ export GRR_DEFINITION_FILE=/path/to/my_grr_definition.yaml
       $ grr_browse

4. **The** ``~/.grr_definition.yaml`` **file** in your home directory — the
   default configuration file. If it exists and none of the options above are
   set, the tools use it automatically. This is the most common way to
   configure your default GRRs once and have every tool pick them up.

5. **The built-in default** — if none of the above is present, GAIn falls back
   to the public `IossifovLab GRR <https://grr.iossifovlab.com/>`_. This is
   equivalent to the following configuration:

   .. code-block:: yaml

       id: main-GRR
       type: http
       url: https://grr.iossifovlab.com


Configuration file structure
----------------------------

A GRR configuration file is a YAML mapping describing a single repository. Every
repository has a required ``type`` and an ``id``, plus additional fields that
depend on the type. A repository can be a *real* repository (a local directory
or a remote URL) or a ``group`` that combines several child repositories.

Common fields
^^^^^^^^^^^^^

    | **id** (string): Identifier for the repository. Used in log messages and to refer to the repository.
    | **type** (string, required): One of ``group``, ``directory``, ``url``, ``http``, ``s3``, or ``embedded`` (see below).
    | **cache_dir** (string, optional): Directory used to cache downloaded resources locally. May be added to any repository type, including a ``group`` (see `Resource caching`_).

Repository types
^^^^^^^^^^^^^^^^

``directory`` — a local repository on disk
    A GRR stored in a local directory.

    | **directory** (string, required): **Absolute** path to a local directory containing the resources. A relative path is rejected.

    .. code-block:: yaml

        id: My_First_GRR
        type: directory
        directory: /home/user/grrs/My_First_GRR

    The aliases ``dir`` and ``file`` are accepted as synonyms of ``directory``.

``url`` — a remote repository (HTTP, HTTPS, or S3)
    The general-purpose remote repository type. The scheme of the URL selects
    the protocol; ``http``, ``https``, and ``s3`` are supported.

    | **url** (string, required): Base URL of the remote repository.

    .. code-block:: yaml

        id: main-GRR
        type: url
        url: https://grr.iossifovlab.com

``http`` — a remote HTTP(S) repository
    Like ``url`` but restricted to ``http`` / ``https`` URLs.

    | **url** (string, required): Base URL of the remote repository.

``s3`` — a remote S3 repository
    Like ``url`` but restricted to ``s3`` URLs.

    | **url** (string, required): ``s3://`` URL of the remote repository.

``embedded`` — an in-memory repository
    A repository whose resources are defined inline in the configuration. This
    is used mainly for testing and small examples.

    | **content** (mapping, required): Nested dictionary describing files and directories. Directory values are nested mappings; file values are file contents.

    The alias ``memory`` is accepted as a synonym of ``embedded``.

``group`` — a collection of repositories
    Combines several repositories and searches them **in the order they appear**
    in ``children``. When a resource ID is requested, the group queries each
    child in turn and returns the first match. Groups can be nested.

    | **children** (list, required): A list of repository configurations (each a real repository or another ``group``).


Search order
^^^^^^^^^^^^

Within a ``group``, repositories are searched top to bottom and the **first**
repository that contains the requested resource wins. Order your ``children``
accordingly — for example, list a local directory before a remote repository if
you want your local copies to take precedence, or after it if the remote should
be authoritative.


Resource caching
----------------

Many genomic resources are large (often hundreds of MB to many GB), and
repeatedly downloading or streaming them from a remote GRR can be slow and
network-dependent. Adding a ``cache_dir`` to a repository tells GAIn to cache
resources locally before using them.

With caching enabled, the first use of a resource may take longer while GAIn
downloads it into ``cache_dir``; after that, GAIn reuses the cached copy, which
is typically much faster and avoids repeated network transfers. The tradeoff is
disk usage, so choose a ``cache_dir`` location with enough capacity.

``cache_dir`` can be attached to any repository, **including a** ``group``. When
attached to a group, it caches every resource served by that group — a
convenient way to put a single cache in front of several remote repositories at
once.


A complete annotated example
----------------------------

The configuration below covers most of the features described above. It defines
a top-level group, ``my_GRRs``, with two children searched in order:

1. ``remote_GRRs`` — a nested group of two remote (``url``) repositories that
   share a single cache directory. Because ``cache_dir`` is set on the group,
   resources from **both** remote repositories are cached under
   ``remote_grr_cache``.
2. ``My_First_GRR`` — a local ``directory`` repository.

When a resource ID is requested, GAIn first searches ``main-GRR``, then
``GRR-ENCODE`` (both via the shared cache), and finally the local
``My_First_GRR``; the first match is returned.

.. code-block:: yaml

    type: group
    id: "my_GRRs"
    children:
    - type: group
      id: "remote_GRRs"
      cache_dir: "<path_to_cache>/remote_grr_cache"   # caches both remote GRRs below
      children:
      - id: "main-GRR"
        type: "url"
        url: "https://grr.iossifovlab.com"

      - id: "GRR-ENCODE"
        type: "url"
        url: "https://grr-encode.iossifovlab.com"

    - id: "My_First_GRR"
      type: "directory"
      directory: "<path_to_My_First_GRR>/My_First_GRR"   # must be an absolute path

To use this configuration, save it as ``~/.grr_definition.yaml`` (so every tool
picks it up automatically), point ``GRR_DEFINITION_FILE`` at it, or pass it
explicitly with ``-g``:

.. code-block:: bash

    $ grr_browse -g my_grr_definition.yaml
