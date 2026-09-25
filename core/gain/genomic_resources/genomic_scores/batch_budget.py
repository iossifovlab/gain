"""How many rows one bulk column-array batch reads.

A bulk read (:meth:`GenomicScore.fetch_region_value_arrays()
<.base.GenomicScore.fetch_region_value_arrays>` and the allele read built on
the same column loop) materialises every cell of the columns it fetches for
the rows of a batch, then parses them while the raw cells are still alive.
Its peak memory therefore grows with rows x columns, not with rows alone, and
a row count on its own lets a score declaring hundreds of columns build a
batch of tens of millions of cells.  The rule here bounds the cells instead.
"""

#: The most raw cells -- rows x the distinct columns a read fetches -- one
#: bulk batch may hold, whatever row count its caller asked for.  It bounds
#: the fetch, not the parse: score ids sharing one payload column each still
#: get a parsed array of their own.  2M cells keeps the full
#: 100,000-row default batch for a read of up to 20 columns, and gives a
#: 454-column read about 4,400 rows.
VALUE_ARRAYS_CELL_BUDGET = 2_000_000


def value_arrays_batch_rows(batch_size: int, column_count: int) -> int:
    """The rows one bulk batch reads: ``batch_size``, capped by the budget.

    The cap divides :data:`VALUE_ARRAYS_CELL_BUDGET` by the number of columns
    the read fetches -- every one of them, a non-score column such as an
    allele's ``reference``/``alternative`` included, since each costs a raw
    cell per row all the same.  Never below one row, so a read wider than the
    whole budget still makes progress.
    """
    if column_count <= 0:
        return batch_size
    return max(1, min(batch_size, VALUE_ARRAYS_CELL_BUDGET // column_count))
