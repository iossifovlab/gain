Testing an annotator
====================

This page tests the ``FollowupAnnotator`` of :doc:`writing_an_annotator`,
the class from "A minimal annotator" on that page. Every example below is
part of a pytest module that runs in GAIn's own test suite, and it uses
the declared API only. The test module defines ``FollowupAnnotator`` and
``build_followup_annotator`` exactly as that page shows them.

Registering in a test
---------------------

A pipeline resolves an annotator's type through the annotator registry. An
installed package puts its type there through an entry point
(:doc:`plugins`). A test runs the pipeline in its own process, so it does
not need an installed entry point:
:func:`~gain.annotation.annotation_factory.register_annotator_factory`
adds the annotator factory to the registry of that process. Call it at
import time of the test module, so the type is registered before the
first test loads a pipeline:

.. literalinclude:: ../../../../core/tests/small/annotation/test_docs_testing_an_annotator.py
   :language: python
   :start-after: # The docs example of the registration starts here.
   :end-before: # The docs example of the registration ends here.

The "Discovering" section of :doc:`plugins` says why this is not a
substitute for the entry point in a package meant to be installed.

A unit test with a seeded context
---------------------------------

The ``followup`` annotator reads only the context, so its unit test needs
no genomic resource. The pipeline loader still takes a repository: an
empty directory repository in ``tmp_path`` is enough. The test loads a
one-annotator pipeline and passes the context that the earlier annotators
would have produced as the second argument of
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.annotate`:

.. literalinclude:: ../../../../core/tests/small/annotation/test_docs_testing_an_annotator.py
   :language: python
   :start-after: # The docs example of the unit test starts here.
   :end-before: # The docs example of the unit test ends here.

The pipeline answers the context with the annotator's attributes merged
into it. With the seeded context, the first answer is
``{'phyloP7way': 1.5, 'clinical_significance': 'Pathogenic', 'followup': 'yes'}``.
An empty context gives ``{'followup': 'no'}``, and a ``None`` annotatable
gives ``{'followup': None}``.

An integration test over a resource
-----------------------------------

An integration test runs the annotator after the annotator that produces
its input. Here a ``position_score_annotator`` produces ``phyloP7way``
from a position score that the test writes into ``tmp_path``. The score
table is a plain tab-separated file: ``format: tsv`` needs no bgzip and no
tabix index. :ref:`grr-position-scores` describes the keys of the
``genomic_resource.yaml``.

.. literalinclude:: ../../../../core/tests/small/annotation/test_docs_testing_an_annotator.py
   :language: python
   :start-after: # The docs example of the resource starts here.
   :end-before: # The docs example of the resource ends here.

The repository is the directory that holds the resource directory, so the
resource id is the name of that directory. The test loads a two-entry
pipeline and asserts that the plugin read what the upstream annotator
produced:

.. literalinclude:: ../../../../core/tests/small/annotation/test_docs_testing_an_annotator.py
   :language: python
   :start-after: # The docs example of the integration test starts here.
   :end-before: # The docs example of the integration test ends here.

What to assert
--------------

* One assertion for each attribute that the annotator declares in
  :meth:`~gain.annotation.annotation_pipeline.Annotator.get_attribute_specs`.
* The answer for a ``None`` annotatable: the base answers ``None`` for
  each attribute.
* :attr:`~gain.annotation.annotation_pipeline.Annotator.used_context_attributes`,
  compared with the context keys that the annotator reads. A reannotation
  builds its dependency graph from this tuple, and a plain annotation run
  does not notice a missing name.

The last test of the module checks the declared attributes and the
declared reads of the built annotator:

.. literalinclude:: ../../../../core/tests/small/annotation/test_docs_testing_an_annotator.py
   :language: python
   :start-after: # The docs example of the declared reads starts here.
   :end-before: # The docs example of the declared reads ends here.

Unsupported fixtures
--------------------

``gain.genomic_resources.testing`` holds the fixture builders that GAIn's
own tests use: they are outside the declared API and can change without
notice.
