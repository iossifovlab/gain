# pylint: disable=W0621,C0114,C0116,W0212,W0613

import json
import pathlib
import textwrap
from typing import cast

import pysam
import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    Histogram,
    HistogramConfig,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    load_histogram,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_inmemory_test_resource,
    convert_to_tab_separated,
    setup_directories,
    setup_genome,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
    a_vcf_info_score,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.executor import (
    TaskGraphExecutor,
)
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor


def test_unpack_score_defs_classifies_histograms() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: float_score
                  name: float_score
                  type: float
                - id: str_score
                  name: str_score
                  type: str
                - id: null_score
                  name: null_score
                  type: float
                  histogram:
                    type: "null"
                    reason: disabled
                - id: preset_score
                  name: preset_score
                  type: float
                  histogram:
                    type: number
                    view_range:
                        min: 0.0
                        max: 1.0
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin float_score str_score null_score preset_score
            1     10        0.1         A         0.9        0.5
            """,
        ),
    })

    (
        min_max_scores,
        hist_confs,
    ) = GenomicScoreImplementation._unpack_score_defs(res)

    assert min_max_scores == ["float_score"]
    assert isinstance(hist_confs["float_score"], NumberHistogramConfig)
    assert not hist_confs["float_score"].has_view_range()
    assert isinstance(hist_confs["str_score"], CategoricalHistogramConfig)
    assert isinstance(hist_confs["null_score"], NullHistogramConfig)
    preset_hist = cast(NumberHistogramConfig, hist_confs["preset_score"])
    assert preset_hist.view_range == (0.0, 1.0)


def test_update_hist_confs_sets_view_range() -> None:
    hist_confs = {"score": NumberHistogramConfig((None, None))}
    minmax = {"score": MinMaxValue("score", 1.0, 5.0)}

    result = GenomicScoreImplementation._update_hist_confs(
        cast(dict[str, HistogramConfig], hist_confs),
        minmax,
    )

    updated_hist = cast(NumberHistogramConfig, result["score"])
    assert updated_hist.view_range == (1.0, 5.0)


def test_update_hist_confs_nullifies_on_nan() -> None:
    hist_confs = {"score": NumberHistogramConfig((None, None))}
    minmax = {"score": MinMaxValue("score")}

    result = GenomicScoreImplementation._update_hist_confs(
        cast(dict[str, HistogramConfig], hist_confs),
        minmax,
    )

    assert isinstance(result["score"], NullHistogramConfig)


def test_get_reference_genome_cached(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path,
        {
            "score": {
                "genomic_resource.yaml": textwrap.dedent(
                    """
                    type: position_score
                    table:
                        filename: data.txt
                    scores:
                        - id: value
                          name: value
                          type: float
                    meta:
                        labels:
                            reference_genome: ref
                    """,
                ),
                "data.txt": convert_to_tab_separated(
                    """
                    chrom pos_begin value
                    1     10        0.1
                    """,
                ),
            },
            "ref": {
                "genomic_resource.yaml": "{type: genome, filename: genome.fa}",
            },
        },
    )
    setup_genome(tmp_path / "ref" / "genome.fa", ">chr1\nAC\n")

    grr = build_filesystem_test_repository(tmp_path)

    GenomicScoreImplementation._REF_GENOME_CACHE.clear()
    ref = GenomicScoreImplementation._get_reference_genome_cached(grr, "ref")

    assert ref is not None
    assert "ref" in GenomicScoreImplementation._REF_GENOME_CACHE
    cached = GenomicScoreImplementation._get_reference_genome_cached(
        grr,
        "ref",
    )
    assert cached is ref
    assert (
        GenomicScoreImplementation._get_reference_genome_cached(None, "ref")
        is None
    )


def test_get_chrom_regions_region_size_zero() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  name: score
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin score
            1     10        0.1
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)
    impl.score.open()
    regions = impl._get_chrom_regions(0)

    # One UNBOUNDED region per contig.  This used to be a single
    # ``Region(None, None, None)`` -- a null contig smuggled through as a
    # region -- which nothing can consume now that a contig is required.
    # ``split_into_regions`` already answered a size of 0 with
    # ``[Region(chrom)]``, so removing the special case left the general
    # path doing the right thing.
    assert [r.chrom for r in regions] == ["1"]
    assert all(r.start is None and r.stop is None for r in regions)


def test_get_chrom_regions_skips_empty_mapped_contig(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A chrom_mapping.filename maps 'kept' onto a file contig with data rows
    # and 'empty' onto one with none.  'empty' is a known-but-empty contig:
    # the in-memory table yields no records for it, so there is no chromosome
    # length to compute -- the contig must be warned about and SKIPPED, not
    # blow up the statistics build.
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
                chrom_mapping:
                    filename: chrom_map.txt
            scores:
                - id: score
                  name: score
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin score
            chr1  10        0.1
            """,
        ),
        "chrom_map.txt": convert_to_tab_separated(
            """
            chrom   file_chrom
            kept    chr1
            empty   chr99
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)
    impl.score.open()

    with caplog.at_level("WARNING"):
        regions = impl._get_chrom_regions(1000)

    assert {region.chrom for region in regions} == {"kept"}
    assert "unable to find chromosome length for empty" in caplog.text


def test_add_statistics_build_tasks_creates_min_max_tasks() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            1     11        0.2
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)

    tasks = impl.create_statistics_build_tasks(region_size=10)

    assert len(tasks) == 6
    save_task = tasks[-1]
    assert save_task.func is \
        GenomicScoreImplementation._merge_and_save_histograms

    task_ids = {t.task.task_id for t in tasks}
    assert any("calculate_min_max" in task_id for task_id in task_ids)
    merge_task = next(
        t for t in tasks if t.task.task_id.endswith("_merge_min_max")
    )
    calc_task = next(
        t
        for t in tasks
        if t.task.task_id.startswith("_calculate_histogram")
    )

    assert merge_task.task in calc_task.deps


def test_add_statistics_tasks_skip_min_max_when_hist_has_range() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
                  histogram:
                    type: number
                    view_range:
                        min: 0.0
                        max: 1.0
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)

    tasks = impl.create_statistics_build_tasks(region_size=10)

    task_ids = {t.task.task_id for t in tasks}
    assert not any("merge_min_max" in task_id for task_id in task_ids)
    calc_task = next(
        t
        for t in tasks
        if t.task.task_id.startswith("_calculate_histogram")
    )
    assert not calc_task.deps


def test_add_min_max_tasks_builds_graph() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)

    calc_tasks = impl.create_statistics_build_tasks(
        region_size=10,
    )

    assert len(calc_tasks) == 4
    calculate_task = calc_tasks[-1]
    assert calculate_task.func is \
        GenomicScoreImplementation._merge_and_save_histograms


def test_add_histogram_tasks_skip_null_histograms_and_link_minmax() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)

    calc_tasks = impl.create_statistics_build_tasks(
        region_size=10,
    )

    assert len(calc_tasks) == 4
    calculate_task = calc_tasks[-1]
    assert calculate_task.func is \
        GenomicScoreImplementation._merge_and_save_histograms


def test_calc_statistics_hash_includes_expected_fields() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
                  histogram:
                    type: number
                    view_range:
                        min: 0.0
                        max: 1.0
                - id: label
                  name: label
                  type: str
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value label
            1     10        0.1   A
            """,
        ),
    })

    impl = build_score_implementation_from_resource(res)

    blob = impl.calc_statistics_hash()
    payload = json.loads(blob.decode())

    # Table config and files md5 present
    assert payload["config"]["table"]["config"]["filename"] == "data.mem"
    files_md5 = payload["config"]["table"]["files_md5"]
    assert "data.mem" in files_md5
    assert isinstance(files_md5["data.mem"], str)
    assert len(files_md5["data.mem"]) > 0

    # Score configuration mirrors resource definitions
    score_ids = {s["id"] for s in payload["score_config"]}
    assert score_ids == {"value", "label"}


def test_calc_statistics_hash_changes_when_file_changes() -> None:
    res1 = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    res2 = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.2
            """,
        ),
    })

    impl1 = build_score_implementation_from_resource(res1)
    impl2 = build_score_implementation_from_resource(res2)

    h1 = impl1.calc_statistics_hash()
    h2 = impl2.calc_statistics_hash()

    assert h1 != h2


def test_calc_statistics_hash_deterministic_for_same_content() -> None:
    res_a = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    res_b = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })

    impl_a = build_score_implementation_from_resource(res_a)
    impl_b = build_score_implementation_from_resource(res_b)

    assert impl_a.calc_statistics_hash() == impl_b.calc_statistics_hash()


@pytest.fixture
def vcf_allele_score_resource(tmp_path: pathlib.Path) -> GenomicResource:
    root_path = tmp_path / "vcf_score"
    setup_vcf(
        root_path / "score.vcf.gz", """
##fileformat=VCFv4.0
##FILTER=<ID=PASS,Description="All filters passed">
##fileDate=20180418
##source=dbSNP
##dbSNP_BUILD_ID=151
##reference=GRCh38.p7
##phasing=partial
##variationPropertyDocumentationUrl=ftp://ftp.ncbi.nlm.nih.gov/snp/specs/dbSNP_BitField_latest.pdf
##INFO=<ID=RS,Number=1,Type=Integer,Description="dbSNP ID (i.e. rs number)">
##INFO=<ID=RSPOS,Number=1,Type=Integer,Description="Chr position reported in dbSNP">
##INFO=<ID=RV,Number=0,Type=Flag,Description="RS orientation is reversed">
##INFO=<ID=VP,Number=1,Type=String,Description="Variation Property.  Documentation is at ftp://ftp.ncbi.nlm.nih.gov/snp/specs/dbSNP_BitField_latest.pdf">
##INFO=<ID=GENEINFO,Number=1,Type=String,Description="Pairs each of gene symbol:gene id.  The gene symbol and id are delimited by a colon (:) and each pair is delimited by a vertical bar (|)">
##INFO=<ID=dbSNPBuildID,Number=1,Type=Integer,Description="First dbSNP Build for RS">
##INFO=<ID=SAO,Number=1,Type=Integer,Description="Variant Allele Origin: 0 - unspecified, 1 - Germline, 2 - Somatic, 3 - Both">
##INFO=<ID=SSR,Number=1,Type=Integer,Description="Variant Suspect Reason Codes (may be more than one value added together) 0 - unspecified, 1 - Paralog, 2 - byEST, 4 - oldAlign, 8 - Para_EST, 16 - 1kg_failed, 1024 - other">
##INFO=<ID=WGT,Number=1,Type=Integer,Description="Weight, 00 - unmapped, 1 - weight 1, 2 - weight 2, 3 - weight 3 or more">
##INFO=<ID=VC,Number=1,Type=String,Description="Variation Class">
##INFO=<ID=PM,Number=0,Type=Flag,Description="Variant is Precious(Clinical,Pubmed Cited)">
##INFO=<ID=TPA,Number=0,Type=Flag,Description="Provisional Third Party Annotation(TPA) (currently rs from PHARMGKB who will give phenotype data)">
##INFO=<ID=PMC,Number=0,Type=Flag,Description="Links exist to PubMed Central article">
##INFO=<ID=S3D,Number=0,Type=Flag,Description="Has 3D structure - SNP3D table">
##INFO=<ID=SLO,Number=0,Type=Flag,Description="Has SubmitterLinkOut - From SNP->SubSNP->Batch.link_out">
##INFO=<ID=NSF,Number=0,Type=Flag,Description="Has non-synonymous frameshift A coding region variation where one allele in the set changes all downstream amino acids. FxnClass = 44">
##INFO=<ID=NSM,Number=0,Type=Flag,Description="Has non-synonymous missense A coding region variation where one allele in the set changes protein peptide. FxnClass = 42">
##INFO=<ID=NSN,Number=0,Type=Flag,Description="Has non-synonymous nonsense A coding region variation where one allele in the set changes to STOP codon (TER). FxnClass = 41">
##INFO=<ID=REF,Number=0,Type=Flag,Description="Has reference A coding region variation where one allele in the set is identical to the reference sequence. FxnCode = 8">
##INFO=<ID=SYN,Number=0,Type=Flag,Description="Has synonymous A coding region variation where one allele in the set does not change the encoded amino acid. FxnCode = 3">
##INFO=<ID=U3,Number=0,Type=Flag,Description="In 3' UTR Location is in an untranslated region (UTR). FxnCode = 53">
##INFO=<ID=U5,Number=0,Type=Flag,Description="In 5' UTR Location is in an untranslated region (UTR). FxnCode = 55">
##INFO=<ID=ASS,Number=0,Type=Flag,Description="In acceptor splice site FxnCode = 73">
##INFO=<ID=DSS,Number=0,Type=Flag,Description="In donor splice-site FxnCode = 75">
##INFO=<ID=INT,Number=0,Type=Flag,Description="In Intron FxnCode = 6">
##INFO=<ID=R3,Number=0,Type=Flag,Description="In 3' gene region FxnCode = 13">
##INFO=<ID=R5,Number=0,Type=Flag,Description="In 5' gene region FxnCode = 15">
##INFO=<ID=OTH,Number=0,Type=Flag,Description="Has other variant with exactly the same set of mapped positions on NCBI refernce assembly.">
##INFO=<ID=CFL,Number=0,Type=Flag,Description="Has Assembly conflict. This is for weight 1 and 2 variant that maps to different chromosomes on different assemblies.">
##INFO=<ID=ASP,Number=0,Type=Flag,Description="Is Assembly specific. This is set if the variant only maps to one assembly">
##INFO=<ID=MUT,Number=0,Type=Flag,Description="Is mutation (journal citation, explicit fact): a low frequency variation that is cited in journal and other reputable sources">
##INFO=<ID=VLD,Number=0,Type=Flag,Description="Is Validated.  This bit is set if the variant has 2+ minor allele count based on frequency or genotype data.">
##INFO=<ID=G5A,Number=0,Type=Flag,Description=">5% minor allele frequency in each and all populations">
##INFO=<ID=G5,Number=0,Type=Flag,Description=">5% minor allele frequency in 1+ populations">
##INFO=<ID=HD,Number=0,Type=Flag,Description="Marker is on high density genotyping kit (50K density or greater).  The variant may have phenotype associations present in dbGaP.">
##INFO=<ID=GNO,Number=0,Type=Flag,Description="Genotypes available. The variant has individual genotype (in SubInd table).">
##INFO=<ID=KGPhase1,Number=0,Type=Flag,Description="1000 Genome phase 1 (incl. June Interim phase 1)">
##INFO=<ID=KGPhase3,Number=0,Type=Flag,Description="1000 Genome phase 3">
##INFO=<ID=CDA,Number=0,Type=Flag,Description="Variation is interrogated in a clinical diagnostic assay">
##INFO=<ID=LSD,Number=0,Type=Flag,Description="Submitted from a locus-specific database">
##INFO=<ID=MTP,Number=0,Type=Flag,Description="Microattribution/third-party annotation(TPA:GWAS,PAGE)">
##INFO=<ID=OM,Number=0,Type=Flag,Description="Has OMIM/OMIA">
##INFO=<ID=NOC,Number=0,Type=Flag,Description="Contig allele not present in variant allele list. The reference sequence allele at the mapped position is not present in the variant allele list, adjusted for orientation.">
##INFO=<ID=WTD,Number=0,Type=Flag,Description="Is Withdrawn by submitter If one member ss is withdrawn by submitter, then this bit is set.  If all member ss' are withdrawn, then the rs is deleted to SNPHistory">
##INFO=<ID=NOV,Number=0,Type=Flag,Description="Rs cluster has non-overlapping allele sets. True when rs set has more than 2 alleles from different submissions and these sets share no alleles in common.">
##FILTER=<ID=NC,Description="Inconsistent Genotype Submission For At Least One Sample">
##INFO=<ID=CAF,Number=.,Type=String,Description="An ordered, comma delimited list of allele frequencies based on 1000Genomes, starting with the reference allele followed by alternate alleles as ordered in the ALT column. Where a 1000Genomes alternate allele is not in the dbSNPs alternate allele set, the allele is added to the ALT column. The minor allele is the second largest value in the list, and was previuosly reported in VCF as the GMAF. This is the GMAF reported on the RefSNP and EntrezSNP pages and VariationReporter">
##INFO=<ID=COMMON,Number=1,Type=Integer,Description="RS is a common SNP.  A common SNP is one that has at least one 1000Genomes population with a minor allele of frequency >= 1% and for which 2 or more founders contribute to that minor allele frequency.">
##INFO=<ID=TOPMED,Number=.,Type=String,Description="An ordered, comma delimited list of allele frequencies based on TOPMed, starting with the reference allele followed by alternate alleles as ordered in the ALT column. The TOPMed minor allele is the second largest value in the list.">
##contig=<ID=chr17>
##bcftools_viewVersion=1.22+htslib-1.22.1
##bcftools_viewCommand=view All_20180418_chr.vcf.gz chr17:29999993-30000000; Date=Thu Dec 11 12:03:13 2025
#CHROM POS      ID           REF                  ALT QUAL FILTER  INFO
chr17  29999973 rs1205271212 A                    G       .       .       RS=1205271212;RSPOS=29999973;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000000305000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;REF;SYN;ASP;TOPMED=0.99999203618756371,0.00000796381243628
chr17  29999977 rs1251997933 G                    A       .       .       RS=1251997933;RSPOS=29999977;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000100005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;DSS;ASP
chr17  29999977 rs1457635223 GTAAGAGTTACATTATTTAA G       .       .       RS=1457635223;RSPOS=29999978;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000100005000002000200;GENEINFO=EFCAB5:374786;WGT=1;VC=DIV;DSS;ASP
chr17  29999981 rs764366409  G                    C       .       .       RS=764366409;RSPOS=29999981;dbSNPBuildID=144;SSR=0;SAO=0;VP=0x050000080005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;INT;ASP
chr17  29999983 rs1197780623 G                    C       .       .       RS=1197780623;RSPOS=29999983;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000080005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;INT;ASP;TOPMED=0.99999203618756371,0.00000796381243628
chr17  29999987 rs1246911347 C                    T       .       .       RS=1246911347;RSPOS=29999987;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000080005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;INT;ASP
chr17  29999992 rs1455026420 T                    C       .       .       RS=1455026420;RSPOS=29999992;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000080005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;INT;ASP
chr17  29999993 rs1195812073 T                    C       .       .       RS=1195812073;RSPOS=29999993;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000080005000002000100;GENEINFO=EFCAB5:374786;WGT=1;VC=SNV;INT;ASP
chr17  29999999 rs1388869160 TTTGAA               T       .       .       RS=1388869160;RSPOS=30000000;dbSNPBuildID=151;SSR=0;SAO=0;VP=0x050000080005000002000200;GENEINFO=EFCAB5:374786;WGT=1;VC=DIV;INT;ASP""")  # noqa
    setup_directories(
        root_path,
        {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: score.vcf.gz
                    index_filename: score.vcf.gz.tbi

                scores:
                  - id: RS
                    column_name: RS
                    type: int
                    desc: dbSNP ID (i.e. rs number)
                    histogram:
                      type: "null"
                      reason: disabled

                  - id: RSPOS
                    column_name: RSPOS
                    type: int
                    desc: Chr position reported in dbSNP
                    histogram:
                      type: "null"
                      reason: disabled

                merge_vcf_scores: true

                default_annotation:
                  - source: RS
                    name: dbSNP_RS
            """),
        },
    )

    grr = build_filesystem_test_repository(tmp_path)
    return grr.get_resource("vcf_score")


@pytest.fixture
def executor() -> TaskGraphExecutor:
    return SequentialExecutor()


def test_statistics_with_vcf_allele_score(
    vcf_allele_score_resource: GenomicResource,
    executor: TaskGraphExecutor,
) -> None:

    impl = build_score_implementation_from_resource(vcf_allele_score_resource)
    graph = TaskGraph()

    tasks = impl.create_statistics_build_tasks(region_size=30_000_000)

    task_ids = {t.task.task_id for t in tasks}
    assert any("calculate_min_max" in task_id for task_id in task_ids)
    graph.add_tasks(tasks)
    task_graph_run(graph, executor)


def test_statistics_with_vcf_allele_score_30_000_000(
    vcf_allele_score_resource: GenomicResource,
    mocker: pytest_mock.MockerFixture,
    executor: TaskGraphExecutor,
) -> None:

    mocker.patch(
        "gain.genomic_resources.implementations.genomic_scores_impl."
        "get_chromosome_length_tabix",
        return_value=30_000_001,
    )
    impl = build_score_implementation_from_resource(vcf_allele_score_resource)
    graph = TaskGraph()

    tasks = impl.create_statistics_build_tasks(region_size=30_000_000)

    task_ids = {t.task.task_id for t in tasks}
    assert any("calculate_min_max" in task_id for task_id in task_ids)

    graph.add_tasks(tasks)
    task_graph_run(graph, executor)


def _build_statistics(
    res: GenomicResource, region_size: int,
) -> None:
    """Run the resource's statistics build tasks to completion."""
    impl = build_score_implementation_from_resource(res)
    graph = TaskGraph()
    graph.add_tasks(
        impl.create_statistics_build_tasks(region_size=region_size),
    )
    task_graph_run(graph, SequentialExecutor())


def _built_histogram(res: GenomicResource, score_id: str) -> Histogram:
    """Read back the histogram a statistics build wrote into ``res``."""
    score = build_score_from_resource(res)
    return load_histogram(res, score.get_histogram_filename(score_id))


def _a_two_chrom_score() -> PositionScoreBuilder:
    return (
        a_position_score()
        .with_score("value", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  value
            chr1   1          10       0.1
            chr1   11         20       0.4
            chr1   21         30       0.9
            chr2   1          10       0.6
            """,
        )
    )


def test_noregion_statistics_build_writes_the_score_histogram(
    tmp_path: pathlib.Path,
) -> None:
    """A ``region_size <= 0`` build computes histograms in a single task.

    ``_do_noregion_histograms`` runs min/max, histogram and save together
    instead of fanning out over regions.  It must still write a histogram
    that spans the whole score.
    """
    res = _a_two_chrom_score().build_resource(tmp_path)

    _build_statistics(res, region_size=0)

    histogram = _built_histogram(res, "value")
    assert isinstance(histogram, NumberHistogram)
    assert (histogram.min_value, histogram.max_value) == (0.1, 0.9)
    # every authored position is counted, across both chromosomes
    assert histogram.bars.sum() == 40


def test_noregion_statistics_build_matches_the_regioned_build(
    tmp_path: pathlib.Path,
) -> None:
    """Splitting the work into regions does not change the result.

    The region fan-out merges per-region min/max and per-region histograms;
    the no-region path computes both in one pass.  Both must arrive at the
    same histogram for the same score.
    """
    builder = _a_two_chrom_score()
    regioned = builder.build_resource(tmp_path / "regioned")
    noregion = builder.build_resource(tmp_path / "noregion")

    _build_statistics(regioned, region_size=10)
    _build_statistics(noregion, region_size=0)

    assert _built_histogram(noregion, "value").serialize() == \
        _built_histogram(regioned, "value").serialize()


def test_collect_index_info_header_includes_score_fields() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
                  desc: a position score
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, _ = impl.collect_index_info()
    assert "score_ids" in header
    assert "score_descriptions" in header


def test_collect_index_info_header_includes_base_fields() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, _ = impl.collect_index_info()
    for field in ("full_id", "id", "type"):
        assert field in header


def test_collect_index_info_score_ids_contains_score_id() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, row = impl.collect_index_info()
    assert "value" in row[header.index("score_ids")]


def test_collect_index_info_score_descriptions_contains_desc() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
                  desc: a position score
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, row = impl.collect_index_info()
    assert "a position score" in row[header.index("score_descriptions")]


def test_collect_index_info_multiple_scores_space_joined() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: alpha
                  name: alpha
                  type: float
                  desc: first score
                - id: beta
                  name: beta
                  type: float
                  desc: second score
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin alpha beta
            1     10        0.1   0.2
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, row = impl.collect_index_info()
    score_ids_value = row[header.index("score_ids")]
    assert "alpha" in score_ids_value
    assert "beta" in score_ids_value
    score_descriptions_value = row[header.index("score_descriptions")]
    assert "first score" in score_descriptions_value
    assert "second score" in score_descriptions_value


def test_collect_index_info_row_length_matches_header() -> None:
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: value
                  name: value
                  type: float
        """,
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin value
            1     10        0.1
            """,
        ),
    })
    impl = build_score_implementation_from_resource(res)
    header, row = impl.collect_index_info()
    assert len(header) == len(row)


# --- tabix index resolution (gain#430) ---------------------------------
#
# A tabix table may be indexed as ``.tbi`` or -- for contigs beyond tabix's
# ~512 Mbp limit -- as ``.csi``.  The implementation must take the index name
# from the resource manifest rather than assuming ``.tbi``.

TABIX_INDEX_DATA = """
    chrom  pos_begin  pos_end  value
    chr1   10         20       0.1
    chr1   30         40       0.2
"""


def a_tabix_position_score(*, csi: bool = False) -> PositionScoreBuilder:
    """A tabix-backed position score, indexed as ``.csi`` on request."""
    return (
        a_position_score()
        .with_tabix(csi=csi)
        .with_zero_based()
        .with_score("value", "float")
        .with_data(TABIX_INDEX_DATA)
    )


def test_files_of_csi_indexed_tabix_score_names_the_csi_index(
    tmp_path: pathlib.Path,
) -> None:
    res = a_tabix_position_score(csi=True).build_resource(tmp_path)

    impl = build_score_implementation_from_resource(res)

    assert impl.files == {"data.txt.gz", "data.txt.gz.csi"}


def test_statistics_hash_of_csi_indexed_score_carries_the_csi_md5(
    tmp_path: pathlib.Path,
) -> None:
    res = a_tabix_position_score(csi=True).build_resource(tmp_path)
    impl = build_score_implementation_from_resource(res)

    files_md5 = json.loads(
        impl.calc_statistics_hash(),
    )["config"]["table"]["files_md5"]

    assert files_md5 == {
        "data.txt.gz": res.get_manifest()["data.txt.gz"].md5,
        "data.txt.gz.csi": res.get_manifest()["data.txt.gz.csi"].md5,
    }


def test_csi_indexed_score_fetches_the_same_region_as_its_tbi_twin(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("csi_score", a_tabix_position_score(csi=True))
        .with_resource("tbi_score", a_tabix_position_score())
        .build_repo(tmp_path)
    )

    def fetch_first_region(resource_id: str) -> list:
        score = build_score_from_resource(repo.get_resource(resource_id))
        with score.open():
            return list(score.fetch_region("chr1", 11, 20, ["value"]))

    assert fetch_first_region("csi_score") == [(11, 20, [0.1])]
    assert fetch_first_region("csi_score") == fetch_first_region("tbi_score")


def test_files_warns_and_omits_index_when_manifest_records_none(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a_grr().with_resource(
        "unindexed_score", a_tabix_position_score(),
    ).build_repo(tmp_path)
    (tmp_path / "unindexed_score" / "data.txt.gz.tbi").unlink()
    res = build_filesystem_test_repository(tmp_path).get_resource(
        "unindexed_score")
    impl = build_score_implementation_from_resource(res)

    with caplog.at_level("WARNING"):
        files = impl.files

    assert files == {"data.txt.gz"}
    assert "unindexed_score" in caplog.text


def test_files_prefers_tbi_when_the_manifest_records_both_indexes(
    tmp_path: pathlib.Path,
) -> None:
    a_grr().with_resource(
        "both_indexes", a_tabix_position_score(),
    ).build_repo(tmp_path)
    # Add a second, .csi index alongside the .tbi the builder wrote.
    pysam.tabix_index(  # pylint: disable=no-member
        str(tmp_path / "both_indexes" / "data.txt.gz"),
        seq_col=0, start_col=1, end_col=2, csi=True)
    res = build_filesystem_test_repository(tmp_path).get_resource(
        "both_indexes")
    assert "data.txt.gz.tbi" in res.get_manifest()
    assert "data.txt.gz.csi" in res.get_manifest()

    impl = build_score_implementation_from_resource(res)

    assert impl.files == {"data.txt.gz", "data.txt.gz.tbi"}


# ``VCFGenomicPositionTable`` subclasses ``TabixGenomicPositionTable``, and
# its chromosome listing routes through ``open_tabix_file`` -- so a
# .csi-indexed VCF score exercises the same resolution.
VCF_INDEX_DATA = """
##fileformat=VCFv4.2
##INFO=<ID=value,Number=A,Type=Float,Description="value">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      value=0.1
chr1   30  .  C   G   .    .      value=0.2
"""


def test_csi_indexed_vcf_score_reads_back_like_its_tbi_twin(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "csi_vcf",
            a_vcf_info_score().with_data(VCF_INDEX_DATA).with_csi_index())
        .with_resource(
            "tbi_vcf", a_vcf_info_score().with_data(VCF_INDEX_DATA))
        .build_repo(tmp_path)
    )

    def read(resource_id: str) -> tuple[list[str], list]:
        score = build_score_from_resource(repo.get_resource(resource_id))
        with score.open():
            return (
                score.get_all_chromosomes(),
                list(score.fetch_region("chr1", 10, 10, ["value"])),
            )

    impl = build_score_implementation_from_resource(repo.get_resource(
        "csi_vcf"))
    assert impl.files == {"data.vcf.gz", "data.vcf.gz.csi"}
    assert read("csi_vcf")[0] == ["chr1"]
    assert read("csi_vcf") == read("tbi_vcf")
