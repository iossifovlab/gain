GRR configuration files
=======================

A GRR configuration file (also called a *GRR definition file*) is a small YAML
file that tells the GAIn command-line tools — :doc:`grr_browse <cli/grr_browse>`,
:doc:`grr_manage <cli/grr_manage>`,
:doc:`annotate_tabular <cli/annotate_tabular>`,
:doc:`annotate_vcf <cli/annotate_vcf>`, ``annotate_variant_effects``, and the
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
repository has a required ``type`` and an optional ``id``, plus additional
fields that depend on the type. A repository can be a *real* repository (a
local directory or a remote URL) or a ``group`` that combines several child
repositories.

Common fields
^^^^^^^^^^^^^

    | **id** (string, optional): Identifier for the repository. Used in log messages, to refer to the repository, and — when ``cache_dir`` is set — as the name of the repository's directory inside the cache. A repository that omits it gets a deterministic id derived from its ``url`` or ``directory`` (or from its type, for an ``embedded`` or ``group`` repository); naming it explicitly is recommended, because the synthesised name is what a populated cache directory is called. Release 2026.7.6 moved the cache of some repositories that omit ``id``: see :ref:`cache_relocation_2026_7_6`.
    | **type** (string, required): One of ``group``, ``directory``, ``url``, ``http``, ``s3``, or ``embedded`` (see below).
    | **cache_dir** (string, optional): Path to a **local filesystem** directory used to cache downloaded resources. May be added to any repository type, including a ``group`` (see `Resource caching`_). It must be a plain path, not a URL; a remote cache target is not supported.

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
    | **user** (string, optional): User for basic http authentication
    | **password** (string, optional): password for basic http authentication

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

    The children of a group must have **distinct ids**; a group whose children
    share an id is rejected when the configuration is loaded. Spelling ``id``
    on a child stays optional — a child that omits it gets a deterministic id
    derived from its ``url`` or ``directory`` (or from its position, for an
    ``embedded`` child or a nested ``group``) — but listing the same
    repository twice is an error, and naming each child explicitly is
    recommended.


Search order
^^^^^^^^^^^^

Within a ``group``, repositories are searched top to bottom and the **first**
repository that contains the requested resource wins. Order your ``children``
accordingly — for example, list a local directory before a remote repository if
you want your local copies to take precedence, or after it if the remote should
be authoritative.


.. _grr-configuration-caching:

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

The cache must be on a **local filesystem**. ``cache_dir`` is a plain directory
path, never a URL: GAIn serialises concurrent downloads into the cache with a
lockfile, which only provides mutual exclusion locally. A ``cache_dir`` that
carries a URL scheme (``s3://bucket/cache``, ``http://host/cache``, ...) is
rejected when the repository is built. Only the cache is constrained -- the
repository being cached may be remote (``s3``, ``http``, ``url``).

``cache_dir`` can be attached to any repository, **including a** ``group``. When
attached to a group, it caches every resource served by that group — a
convenient way to put a single cache in front of several remote repositories at
once.

GAIn keeps the resources of each cached repository in its own subdirectory,
``<cache_dir>/<repository id>/``. The repository id is the ``id`` of the
repository, or the id that GAIn synthesises when the ``id`` is missing (see
`Common fields`_).

.. _cache_relocation_2026_7_6:

Upgrading from a release before 2026.7.6
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Release 2026.7.6 moved some cached resources to a new location. This
subsection is for an operator who upgrades GAIn from a release before
2026.7.6 and keeps the old ``cache_dir``.

**Which definitions are affected.** A definition is affected when it has one
of these repositories:

- a repository inside a ``group``, at any depth, that omits ``id`` or sets
  ``id: ""``, and has cached resources. The cache can come from the
  ``cache_dir`` of the repository itself or from the ``cache_dir`` of any
  enclosing ``group``.
- a top-level cached repository with ``id: ""``

Before 2026.7.6, these repositories had an empty id and cached their resources
directly into the root of ``cache_dir``. A definition that gives each
repository a non-empty ``id`` is not affected.

**What changed.** Since 2026.7.6, every repository has a non-empty id. The
resources of an affected repository move from the root of ``cache_dir`` into
``<cache_dir>/<repository id>/``. After the upgrade, GAIn downloads these
resources again one time.

**What stays behind.** The old resource directories stay at the root of
``cache_dir``. GAIn does not use them, and GAIn does not delete them. They use
disk space until you delete them.

For example, a cached ``group`` has ``cache_dir: /data/grr_cache`` and one
``directory`` child with ``directory: /data/grr`` and no ``id``. Release
2026.7.5 cached the resource ``demo_score`` of this child at the root of
``cache_dir``. After one read with the new release, the cache holds two
copies:

.. code-block:: text

    /data/grr_cache/demo_score/                     <- old copy, not used
    /data/grr_cache/data_grr_ecff7cb0/demo_score/   <- new copy

**Find the current cache directories.** The script below prints the cache
directory of each cached repository in one or more GRR definitions. It
includes the ids that GAIn synthesises. When a cached repository is inside
another cached repository, the outer cache keeps the resources in
``<cache_dir>/<repository id>.cached/``, and the script prints this directory
too. Save it as ``list_cache_dirs.py`` and
run it with the upgraded GAIn:

.. code-block:: python

    """Print the cache directories that the current GAIn version uses."""
    import sys

    from gain.genomic_resources.cached_repository import (
        GenomicResourceCachedRepo,
    )
    from gain.genomic_resources.group_repository import (
        GenomicResourceGroupRepo,
    )
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
        load_definition_file,
    )


    def walk(repo, cache_dirs=()):
        if isinstance(repo, GenomicResourceCachedRepo):
            cache_dir = repo.cache_url.removeprefix("file://").rstrip("/")
            walk(repo.child, (*cache_dirs, cache_dir))
        elif isinstance(repo, GenomicResourceGroupRepo):
            for child in repo.children:
                walk(child, cache_dirs)
        else:
            # The innermost cache uses the repository id. Each outer cache
            # adds ".cached" to the id of the cache that it wraps.
            for depth, cache_dir in enumerate(reversed(cache_dirs)):
                print(f"{cache_dir}/{repo.repo_id}{'.cached' * depth}")


    for definition_file in sys.argv[1:]:
        walk(build_genomic_resource_repository(
            load_definition_file(definition_file)))

.. code-block:: bash

    $ python list_cache_dirs.py ~/.grr_definition.yaml
    /data/grr_cache/data_grr_ecff7cb0

**Clean up the cache.** Select one of the two options.

- **The simple option.** Stop every process that uses the cache. Then delete
  the full ``cache_dir``. GAIn downloads each resource again when a process
  uses it.
- **The targeted option.** Keep only the directories at the root of
  ``cache_dir`` that have the name of a current repository id. Delete all
  other entries at the root of ``cache_dir``.

The targeted option works only when no ``cache_dir`` is inside another
``cache_dir``. For example, an inner ``cache_dir`` at
``/data/grr_cache/inner`` is not a line in ``keep.txt``, so step 5 deletes
the full inner cache. If one ``cache_dir`` is inside another, use the simple
option or clean up the cache manually.

For the targeted option, do these steps:

1. Find every GRR definition that uses this ``cache_dir``. Two definitions can
   share one ``cache_dir``, and each definition has its own repository ids.
2. Stop every process that uses the cache.
3. Run ``list_cache_dirs.py`` with all of these definitions, and write the
   output to a file:

   .. code-block:: bash

       $ python list_cache_dirs.py first_definition.yaml \
             second_definition.yaml > keep.txt

4. List the entries at the root of ``cache_dir`` that are not in
   ``keep.txt``. Write the ``cache_dir`` path exactly as ``keep.txt`` shows
   it. Examine the list before you delete anything:

   .. code-block:: bash

       $ find /data/grr_cache -mindepth 1 -maxdepth 1 | grep -vxF -f keep.txt

5. Delete these entries:

   .. code-block:: bash

       $ find /data/grr_cache -mindepth 1 -maxdepth 1 \
             | grep -vxF -f keep.txt \
             | while IFS= read -r entry; do rm -rf -- "$entry"; done

If you do not include a definition in step 3, step 5 deletes the cache of
that definition. GAIn then downloads its resources again.

**Recommendation.** Give each repository an explicit ``id``. An explicit id
keeps the name of the cache directory stable and readable. A synthesised id
changes when the ``url`` or ``directory`` of the repository changes, and its
name is hard to recognise.


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
