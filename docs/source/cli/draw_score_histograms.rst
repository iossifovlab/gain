draw_score_histograms
=====================

``draw_score_histograms`` draws the histograms of the scores of the
resources of a GRR. It finds the GRR that starts at the current directory,
or at the ``-R`` repository. It writes the histogram files into the
directory of each score resource. The GRR must be a read-write GRR, for
example a local directory. Use it after you add or change a score resource,
so that the GRR info page of the resource shows the histograms.

Examples
--------

.. code-block:: bash

    draw_score_histograms

Draws the histograms of every score resource of the GRR that contains the
current directory.

.. code-block:: bash

    draw_score_histograms -R /data/grr -r hg38/scores/phyloP100way

Draws the histograms of one score resource of the GRR in ``/data/grr``.

Option reference
----------------

.. argparse::
    :module: gain.genomic_resources.draw_score_histograms
    :func: parse_cli_arguments
    :prog: draw_score_histograms
    :nodescription:
