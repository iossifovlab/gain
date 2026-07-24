"""Model runtime seam for the SpliceAI annotator (issue #297).

``spliceai_annotator.py`` holds all the domain logic and talks to the model
runtime only through the three functions re-exported here
(``spliceai_load_models`` / ``spliceai_predict`` / ``spliceai_close``) plus the
process-wide ``SPLICEAI_MODELS``. This module is the only place that knows
*which* runtime executes the ensemble.

Two backends ship:

* ``tensorflow`` (default) -- the five ``.h5`` files, unchanged; and
* ``onnx`` -- the five converted ``.onnx`` artifacts under ONNX Runtime.

Select with the ``SPLICEAI_BACKEND`` environment variable
(``SPLICEAI_BACKEND=onnx``). It is read once, at import time: the backend is a
process-wide choice, like the models it loads, and switching it mid-process
would leave already-opened annotators holding models from the other runtime.
"""
import importlib
import logging
import os
from types import ModuleType
from typing import cast

import numpy as np

logger = logging.getLogger(__name__)

SPLICEAI_BACKEND_ENV = "SPLICEAI_BACKEND"
DEFAULT_SPLICEAI_BACKEND = "tensorflow"

#: Backend name -> module implementing the model runtime interface.
SPLICEAI_BACKENDS = {
    "tensorflow": ".spliceai_backend_tensorflow",
    "onnx": ".spliceai_backend_onnx",
}


def spliceai_backend_name() -> str:
    """Return the backend selected by ``SPLICEAI_BACKEND``.

    Unknown values raise instead of silently falling back: a typo'd backend
    that quietly ran TensorFlow would make an ONNX CI tier pass without ever
    exercising ONNX.
    """
    name = os.environ.get(
        SPLICEAI_BACKEND_ENV, DEFAULT_SPLICEAI_BACKEND).strip().lower()
    if name not in SPLICEAI_BACKENDS:
        raise ValueError(
            f"unknown SpliceAI backend {name!r} in "
            f"{SPLICEAI_BACKEND_ENV}; expected one of "
            f"{sorted(SPLICEAI_BACKENDS)}")
    return name


def load_spliceai_backend(name: str | None = None) -> ModuleType:
    """Import the backend module, defaulting to the selected one."""
    if name is None:
        name = spliceai_backend_name()
    elif name not in SPLICEAI_BACKENDS:
        raise ValueError(
            f"unknown SpliceAI backend {name!r}; expected one of "
            f"{sorted(SPLICEAI_BACKENDS)}")
    # Logged because it is otherwise invisible which runtime produced a set of
    # annotations -- the two agree numerically, so the output cannot tell you.
    logger.info("using the %s SpliceAI model runtime backend", name)
    return importlib.import_module(SPLICEAI_BACKENDS[name], __package__)


#: The backend name that actually won at import time. `spliceai_backend_name`
#: re-reads the environment on every call, so it answers "what does the
#: environment say now", which after import is a different question -- anything
#: setting SPLICEAI_BACKEND late is silently ignored. Ask this instead when you
#: need to know which runtime is loaded (a CI tier asserting it really ran
#: ONNX, a log line, a bug report).
SPLICEAI_BACKEND_NAME = spliceai_backend_name()

#: The module implementing the runtime -- named `_MODULE` so it cannot be
#: confused with SPLICEAI_BACKEND_ENV, the environment variable's *name*.
SPLICEAI_BACKEND_MODULE = load_spliceai_backend(SPLICEAI_BACKEND_NAME)


def spliceai_load_models() -> list:
    """Open SpliceAI annotator implementation."""
    return cast(list, SPLICEAI_BACKEND_MODULE.spliceai_load_models())


def spliceai_close() -> None:
    SPLICEAI_BACKEND_MODULE.spliceai_close()


def spliceai_predict(
    models: list,
    x: np.ndarray,
) -> np.ndarray:
    return cast(
        np.ndarray, SPLICEAI_BACKEND_MODULE.spliceai_predict(models, x))


SPLICEAI_MODELS = spliceai_load_models()
