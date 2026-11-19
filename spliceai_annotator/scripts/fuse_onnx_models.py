#!/usr/bin/env python
"""Fuse the five SpliceAI ONNX members into one graph with a mean head.

Reads the five committed ``spliceai_annotator/models/spliceai{1..5}.onnx``
files and writes ``spliceai_annotator/models/spliceai_fused.onnx``: one graph
with one input, the five members side by side, and an ONNX ``Mean`` node that
averages their outputs element-wise. The ``onnx-fused`` backend runs it as a
single ONNX Runtime session.

The fused graph derives from the ``.onnx`` files, not from the ``.h5`` files,
so this script needs only ``onnx`` from the ``dev`` dependency group. It never
imports TensorFlow, and it runs on an install without the ``tensorflow``
extra. Regenerate the fused graph whenever a member ``.onnx`` changes.

The output is reproducible byte for byte: every name in the graph is derived
from the member's position and its own names, and the producer fields are
fixed. ``tests/test_onnx_fused.py`` regenerates the graph and compares it with
the committed file.

Usage
-----
    python spliceai_annotator/scripts/fuse_onnx_models.py
    python spliceai_annotator/scripts/fuse_onnx_models.py --output x.onnx
"""
import argparse
import pathlib
from collections.abc import Sequence
from itertools import starmap

import onnx
from onnx import TensorProto, helper

MODELS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "spliceai_annotator" / "models"
)
MEMBER_PATHS = tuple(
    MODELS_DIR / f"spliceai{index}.onnx" for index in range(1, 6))
FUSED_PATH = MODELS_DIR / "spliceai_fused.onnx"

INPUT_NAME = "input"
OUTPUT_NAME = "output"
PRODUCER_NAME = "gain-spliceai-annotator/fuse_onnx_models.py"
PRODUCER_VERSION = "1"


def _member_nodes(
    member: onnx.ModelProto, prefix: str,
) -> tuple[list[onnx.NodeProto], list[onnx.TensorProto], str]:
    """The nodes and initializers of ``member``, renamed under ``prefix``.

    Every edge gets the prefix except the member's graph input, which becomes
    the shared ``input`` of the fused graph. Returns the renamed name of the
    member's output as the third item.
    """
    graph = member.graph
    if len(graph.input) != 1 or len(graph.output) != 1:
        raise ValueError(
            f"expected one input and one output per member, got "
            f"{len(graph.input)} and {len(graph.output)}")
    member_input = graph.input[0].name

    def rename(name: str) -> str:
        if not name:
            return name
        if name == member_input:
            return INPUT_NAME
        return prefix + name

    nodes = []
    for source in graph.node:
        if any(attr.type in (onnx.AttributeProto.GRAPH,
                             onnx.AttributeProto.GRAPHS)
               for attr in source.attribute):
            raise ValueError(
                f"node {source.name!r} carries a subgraph; this script does "
                f"not rename edges inside subgraphs")
        node = onnx.NodeProto()
        node.CopyFrom(source)
        node.name = rename(source.name)
        node.input[:] = [rename(name) for name in source.input]
        node.output[:] = [rename(name) for name in source.output]
        nodes.append(node)

    initializers = []
    for source_tensor in graph.initializer:
        tensor = onnx.TensorProto()
        tensor.CopyFrom(source_tensor)
        tensor.name = rename(source_tensor.name)
        initializers.append(tensor)

    return nodes, initializers, rename(graph.output[0].name)


def fuse_models(member_paths: Sequence[pathlib.Path]) -> onnx.ModelProto:
    """Fuse the ONNX members at ``member_paths`` under one ``Mean`` node.

    The members must share their opset imports and their IR version. The
    fused graph takes ``input`` of shape ``(batch, length, 4)`` and returns
    ``output`` of shape ``(batch, positions, 3)``: both the batch axis and the
    length axis stay dynamic.
    """
    members = [onnx.load(str(path)) for path in member_paths]
    if not members:
        raise ValueError("no members to fuse")
    opsets = [
        sorted((o.domain, o.version) for o in member.opset_import)
        for member in members]
    if any(opset != opsets[0] for opset in opsets):
        raise ValueError(f"the members disagree on their opsets: {opsets}")
    ir_versions = {member.ir_version for member in members}
    if len(ir_versions) != 1:
        raise ValueError(
            f"the members disagree on their IR version: {ir_versions}")

    nodes: list[onnx.NodeProto] = []
    initializers: list[onnx.TensorProto] = []
    member_outputs = []
    for position, member in enumerate(members, start=1):
        member_nodes, member_initializers, output = _member_nodes(
            member, f"member{position}/")
        nodes.extend(member_nodes)
        initializers.extend(member_initializers)
        member_outputs.append(output)

    nodes.append(helper.make_node(
        "Mean", member_outputs, [OUTPUT_NAME], name="ensemble_mean"))

    graph = helper.make_graph(
        nodes,
        "spliceai_fused",
        [helper.make_tensor_value_info(
            INPUT_NAME, TensorProto.FLOAT, ["batch", "length", 4])],
        [helper.make_tensor_value_info(
            OUTPUT_NAME, TensorProto.FLOAT, ["batch", "positions", 3])],
        initializer=initializers,
    )
    model = helper.make_model(
        graph,
        opset_imports=list(starmap(helper.make_opsetid, opsets[0])),
        ir_version=ir_versions.pop(),
        producer_name=PRODUCER_NAME,
        producer_version=PRODUCER_VERSION,
    )
    onnx.checker.check_model(model, full_check=True)
    return model


def main(argv: Sequence[str] | None = None) -> None:
    """Write the fused graph of the five committed members."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output", type=pathlib.Path, default=FUSED_PATH,
        help=f"where to write the fused graph (default: {FUSED_PATH})")
    args = parser.parse_args(argv)

    model = fuse_models(MEMBER_PATHS)
    args.output.write_bytes(model.SerializeToString())
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
