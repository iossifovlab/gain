"""Views for pipeline creation and manipulation."""
import asyncio
import time
from concurrent.futures import Future
from pathlib import Path
from typing import ClassVar, TypeVar

import yaml
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpResponse, QueryDict
from gain import logging
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationPreamble,
    AnnotatorInfo,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.pipeline_doc import render_pipeline_doc
from gain.genomic_resources.repository import GenomicResourceRepo
from rest_framework import views
from rest_framework.request import MultiValueDict
from rest_framework.views import Request, Response

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AnnotationMixin,
    AsyncAnnotationBaseView,
    format_config_error,
)
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import (
    BaseUser,
    Pipeline,
    TemporaryPipeline,
    WebAnnotationAnonymousUser,
)
from web_annotation.pipeline_cache import (
    _load_test_build_delay,
    await_build,
)
from web_annotation.pipelines.throttling import PipelineValidationRateThrottle
from web_annotation.validation_cache import ValidationResultCache

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class UserPipeline(AnnotationBaseView):
    """View for saving user annotation pipelines."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def _save_user_pipeline(
        self,
        request: Request,
        config_path: Path,
    ) -> Response | None:
        assert isinstance(request.FILES, MultiValueDict)

        config_file = request.FILES["config"]
        assert isinstance(config_file, UploadedFile)
        try:
            raw_content = config_file.read()
            content = raw_content.decode()
        except UnicodeDecodeError as e:
            logger.exception("Unicode decode error in pipeline config file")
            return Response(
                {"reason": f"Invalid pipeline configuration file: {e!s}"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        # Structural validation only -- this runs on the single shared
        # thread_sensitive sync-view thread under daphne, so building the
        # pipeline against the GRR here serializes every other API request
        # behind it (#150 H1). Deep, resource-resolving validation is deferred
        # to the background pipeline loader scheduled by put_pipeline below;
        # load failures surface to the user via the pipeline load-status
        # channel rather than as a synchronous 400.
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            return Response(
                {"errors": "Invalid configuration"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        # An empty config (None: blank / whitespace / comments-only) is a
        # valid empty pipeline -- the app and the /validate endpoint accept it,
        # and the web_ui saves an empty temp pipeline whenever the editor is
        # cleared (e.g. "New pipeline"). Only reject a non-empty scalar that
        # cannot be a pipeline structure; deeper validation is deferred to the
        # background loader.
        if parsed is not None and not isinstance(parsed, (list, dict)):
            return Response(
                {"errors": "Invalid configuration"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
        except OSError:
            logger.exception("Could not write config file")
            return Response(
                {"reason": "Could not write file!"},
                status=views.status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return None

    def post(self, request: Request) -> Response:
        """Create or update user annotation pipeline"""
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)

        pipeline_id = request.data.get("id")
        temporary = False

        pipeline_name = request.data.get("name")

        if pipeline_id:
            try:
                int(pipeline_id)
            except ValueError:
                temporary = True
                if pipeline_id != request.user.session_id:
                    return Response(
                        {"reason": "Pipeline ID does not match session ID!"},
                        status=views.status.HTTP_400_BAD_REQUEST,
                    )

        if not pipeline_id and not pipeline_name:
            temporary = True

        if temporary:
            pipeline_name = f"pipeline-{request.user.session_id}.yaml"

        if not temporary and pipeline_name in self.grr_pipelines:
            return Response(
                {"reason": (
                    "Pipeline with such name cannot be created or updated!"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        if not temporary and isinstance(
                request.user, WebAnnotationAnonymousUser):
            return Response(
                {"reason": "Only authenticated users can create pipelines!"},
                status=views.status.HTTP_401_UNAUTHORIZED,
            )

        config_filename = f"{pipeline_name}.yaml"

        if pipeline_id:  # Update
            pipeline = request.user.get_temporary_pipeline(pipeline_id)
            if pipeline is None:
                pipeline = request.user.get_pipeline(pipeline_id)
            config_path = Path(str(pipeline.config_path))
        else:  # Create
            assert pipeline_name is not None
            if not temporary:
                config_path = Path(
                    settings.ANNOTATION_CONFIG_STORAGE_DIR,
                    request.user.identifier,
                    config_filename,
                )
                if pipeline_name is not None and Pipeline.objects.filter(
                    owner=request.user.user,
                    name=pipeline_name,
                ):
                    return Response({
                        "reason": (
                            "Pipeline with name "
                            f"{pipeline_name} already exists!"
                        ),
                    }, status=views.status.HTTP_400_BAD_REQUEST)
                pipeline = Pipeline(
                    name=pipeline_name,
                    config_path=config_path,
                    owner=request.user.user.as_owner,
                )
            else:
                config_path = Path(
                    settings.ANNOTATION_CONFIG_STORAGE_DIR,
                    "temporary",
                    config_filename,
                )
                pipeline, _ = TemporaryPipeline.objects.get_or_create(
                    session_id=request.user.session_id,
                    defaults={
                        "name": pipeline_name,
                        "config_path": str(config_path),
                    },
                )

        pipeline_or_response = self._save_user_pipeline(
            request, config_path,
        )
        if isinstance(pipeline_or_response, Response):
            return pipeline_or_response

        pipeline.save()

        self.put_pipeline(
            str(pipeline.id),
            request.user,
        )

        return Response(
            {"id": str(pipeline.pk)},
            status=views.status.HTTP_200_OK,
        )

    def get(self, request: Request) -> Response:
        """Get user annotation pipeline"""
        pipeline_id = request.query_params.get("id")
        if not pipeline_id:
            return Response(
                {"reason": "Pipeline ID not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        try:
            pipeline = request.user.get_temporary_pipeline(pipeline_id)
        except TemporaryPipeline.DoesNotExist:
            pipeline = None
        except ValueError:
            return Response(
                {
                    "reason": (
                        "Temporary pipeline does not match request session ID"
                    ),
                },
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        try:
            pipeline = request.user.get_pipeline(pipeline_id)
        except Pipeline.DoesNotExist:
            return Response(
                {"reason": "Pipeline name not recognized!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        except ValueError:
            return Response(
                {"reason": "Pipeline name not recognized!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        response = {
            "id": pipeline_id,
            "name": pipeline.name,
            "owner": pipeline.owner.identifier,
            "pipeline": Path(pipeline.config_path).read_text("utf-8"),
        }

        return Response(response, status=views.status.HTTP_200_OK)

    def delete(self, request: Request) -> Response:
        """Delete user annotation pipeline"""
        pipeline_id = request.query_params.get("id")
        if not pipeline_id:
            return Response(
                {"reason": "Pipeline name not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        request.user.delete_pipeline(pipeline_id)
        self.lru_cache.unload_pipeline(pipeline_id)

        return Response(status=views.status.HTTP_204_NO_CONTENT)


class ListPipelines(AnnotationBaseView):
    """View for listing all annotation pipelines for files."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def _pipeline_status(
        self, pipeline_id: str,
    ) -> tuple[str, str | None]:
        """Resolve the durable load status for the listing (#155).

        A finished-but-failed deferred build (#150 H1) reads as a distinct
        'failed' carrying an actionable reason, so a refresh does not collapse
        it back to a bare 'unloaded' indistinguishable from never-loaded.
        """
        if self.lru_cache.is_pipeline_loaded(pipeline_id):
            return "loaded", None
        error = self.lru_cache.get_pipeline_error(pipeline_id)
        if error is not None:
            return "failed", format_config_error(error)
        return "unloaded", None

    def _get_grr_pipelines(self) -> list[dict[str, str | None]]:
        result = []
        for pipeline in self.grr_pipelines.values():
            status, error = self._pipeline_status(pipeline["id"])
            result.append({
                "id": pipeline["id"],
                "type": "default",
                "name": pipeline["id"],
                "content": pipeline["content"],
                "status": status,
                "error": error,
            })
        return result

    def _get_user_pipelines(
        self, user: BaseUser,
    ) -> list[dict[str, str | None]]:
        filtered_pipelines = filter(None, user.get_pipelines())

        result = []
        for pipeline in filtered_pipelines:
            status, error = self._pipeline_status(pipeline.identifier)
            result.append({
                "id": str(pipeline.pk),
                "name": pipeline.name,
                "type": "user",
                "content": Path(
                    pipeline.config_path,
                ).read_text(encoding="utf-8"),
                "status": status,
                "error": error,
            })
        return result

    def get(self, request: Request) -> Response:
        """List all available annotation pipelines."""
        pipelines = self._get_grr_pipelines()
        if request.user and request.user.is_authenticated:
            pipelines = pipelines + self._get_user_pipelines(request.user)

        default_pipeline_id = settings.DEFAULT_PIPELINE
        if default_pipeline_id is not None:
            default_index = next(
                (i for i, p in enumerate(pipelines)
                 if p["name"] == default_pipeline_id),
                None,
            )
            if default_index is None:
                return Response(
                    {"reason": f"DEFAULT_PIPELINE '{default_pipeline_id}' "
                     "not found in available pipelines."},
                    status=views.status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            pipelines.insert(0, pipelines.pop(default_index))

        return Response(
            pipelines,
            status=views.status.HTTP_200_OK,
        )


class PipelineDoc(AsyncAnnotationBaseView):
    """View for downloading the annotate_doc HTML for a pipeline.

    Async (#167): the only long pole -- the GRR pipeline build wait -- leaves
    the event loop via ``aget_pipeline``. Converting for *event-loop
    protection* (await the GRR build OFF the loop) and uniformity with the
    other read views (#163/#165/#166); Django ASGI already wraps each sync HTTP
    request in its own ``ThreadSensitiveContext`` (#164), so this is not about
    unblocking a shared sync thread. The doc render touches GRR metadata
    (resource/histogram URLs) and does CPU-bound markdown + Jinja template work,
    so it runs off the loop via ``sync_to_async`` (asgiref default
    thread_sensitive). Build failure -> 400, missing -> 404 mapping is inherited
    from ``aget_pipeline``. No ORM and no ``annotate()`` here, so no dedicated
    executor is needed. adrf dispatches a view async iff *all* its handlers are
    coroutines; this view has only ``get`` so it qualifies.

    The handler returns a bare Django ``HttpResponse`` on the happy path (the
    rendered-doc download) and a DRF ``Response`` on the early ``pipeline_id``
    400. adrf's async dispatch finalizes both through the same DRF
    ``finalize_response`` as the sync path: a bare ``HttpResponse`` is an
    ``HttpResponseBase`` and passes through untouched (the renderer-attachment
    branch only runs for DRF ``Response``), so no special handling is required
    and the response bytes/headers/status are byte-for-byte the prior sync
    output.
    """

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    async def get(self, request: Request) -> Response | HttpResponse:
        """Return an HTML doc for the given pipeline as a download."""
        pipeline_id = request.query_params.get("pipeline_id")
        if not pipeline_id:
            return Response(
                {"reason": "pipeline_id not provided."},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        # Long pole: await the GRR pipeline build OFF the event loop. Build
        # failure -> 400, missing -> 404 mapping comes from aget_pipeline.
        pipeline = await self.aget_pipeline(pipeline_id, request.user)

        # The render touches GRR metadata and does CPU-bound markdown/Jinja
        # work; run it off the loop. Byte-for-byte identical to the prior sync
        # render.
        #
        # The document, and the addresses it carries, belong to ``gain``: the
        # CLI and the ``annotation_pipeline`` resource pages render through
        # the same function (#952). This endpoint wants its default
        # public-mirror policy and names no pipeline file, so it passes
        # nothing but the pipeline. Deviating from the shared default here is
        # what left this endpoint's links on the repository's own url for two
        # months (#841).
        html_doc = await sync_to_async(render_pipeline_doc)(pipeline)

        response = HttpResponse(html_doc, content_type="text/html")
        response["Content-Disposition"] = (
            f'attachment; filename="{pipeline_id}.html"'
        )
        return response


def _count_annotators(content: str) -> int | None:
    """Count the annotators ``content`` declares, without resolving any.

    Returns ``None`` when the config is not countable -- unparsable YAML, or
    a shape that is not a pipeline. Those are configuration errors, and
    reporting them is the job of the parser proper, whose messages callers
    already depend on; this function must not pre-empt them with a message
    of its own.

    A ``yaml.safe_load`` here duplicates the one the parser does moments
    later. That is deliberate: it is linear in the (already bounded) body,
    it touches no GRR, and it is what lets the annotator count be refused
    before any resource is resolved.
    """
    try:
        parsed = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError):
        # RecursionError is not a YAMLError: deeply nested input exhausts
        # the stack inside the composer. It reaches here rather than the
        # view's own error handling because this runs before it, so leaving
        # it uncaught would turn an invalid config into a 500.
        return None

    if isinstance(parsed, dict):
        parsed = parsed.get("annotators")
    if not isinstance(parsed, list):
        return None
    return len(parsed)


class PipelineValidation(AsyncAnnotationBaseView):
    """Validate annotation config.

    Anonymous and unauthenticated by design -- the pipeline editor validates
    for users who have not signed in. That makes the config body the widest
    piece of untrusted input the API accepts, so it is bounded four times
    on the way in: on the size of the request body (iossifovlab/gain#676),
    then on the size of the config it carries, on the number of annotators
    that config declares, and on the number those annotators expand to
    before any of them is built (the last three iossifovlab/gain#635). Each
    is a request-level refusal (400), and each happens before the work it
    bounds. A config that is merely *invalid* keeps the endpoint's 200 +
    ``errors`` contract, which the deferred-load path (#155) shares.

    The body bound is first because it is the only one that can be: the
    three #635 bounds all read ``request.data``, and reading it parses the
    body, so none of them can refuse the parse that produced their own
    input.

    Those four are per-request, and per-request bounds cannot see aggregate
    load: every caller can be within all of them while together they queue
    more work than the validation pool will ever drain. A fifth refusal
    covers that -- ``MAX_VALIDATIONS_IN_FLIGHT``, a 503 with ``Retry-After``
    when the pool is already carrying its bound. It is not a bound on the
    request but on admission, so it sits after the four that are, where a
    request that has an accurate 400 coming still gets it.

    What the bounds do NOT bound is the cost of building the annotators that
    survive all three, and this endpoint builds them itself rather than
    deferring to the background loader the sibling save-pipeline path uses
    (the #150 H1 comment on ``UserPipeline``). Async (#659): the build --
    and the wildcard expansion that gates it, whose cost scales with the
    repository -- are submitted to a dedicated bounded pool and awaited, so
    neither runs on a ``thread_sensitive`` sync-view thread nor on the event
    loop this async view is dispatched on. That thread is shared
    by every synchronous view of a caller that shares one
    ``ThreadSensitiveContext``; under daphne Django gives each HTTP request
    its own instead (measured in ``docs/164-async-read-views-slo.md``), which
    turns the same build into an *unbudgeted thread per anonymous request*.
    Either way it is occupancy an anonymous caller could buy, and the pool
    replaces it with a bound. The HTTP contract is unchanged: the same
    statuses and the same message bodies as before.

    Both of the questions #666 raised about that build are now answered. The
    build stays: the editor's green state promises that the config builds
    against the GRR, and #666 decided that promise is worth what it costs.
    And its result is memoised, by a digest of the config text, so the
    repeat cost of a debounced editing session is a dictionary lookup
    (#833 -- see ``validation_cache`` above and ``_memoise`` below).
    """

    throttle_classes: ClassVar = [PipelineValidationRateThrottle]

    #: The memo (#833). On the class, so the one instance is shared by every
    #: request -- DRF builds a fresh view object per request, and a memo that
    #: died with it would never see a second keystroke.
    #:
    #: Here rather than on ``AnnotationMixin`` because it joins the other
    #: validate-only bounds below (``MAX_CONFIG_LENGTH``, ``MAX_ANNOTATORS``,
    #: ``MAX_EXPANDED_ANNOTATORS``, ``MAX_VALIDATIONS_IN_FLIGHT``). Not
    #: because the mixin holds only shared things -- ``VALIDATE_EXECUTOR``
    #: is up there and has exactly one user, this view -- but because a memo
    #: of *this* endpoint's verdicts on the shared mixin would be visible to
    #: the job, editor and single-allele views, which neither produce nor
    #: consume one.
    validation_cache: ClassVar[ValidationResultCache] = ValidationResultCache(
        capacity=settings.VALIDATION_CACHE_SIZE,
        ttl_seconds=settings.VALIDATION_CACHE_TTL_SECONDS,
    )

    # Nothing on this path bounds the body Django hands to DRF's parsers.
    # ``DATA_UPLOAD_MAX_MEMORY_SIZE`` is consulted by ``HttpRequest.body``
    # and ``.POST``, and DRF's parsers use neither; for multipart, Django
    # bounds non-file field bytes and the file *count* (100), never a
    # part's size. Nor does the client's upload speed bound it: Django's
    # ASGI handler buffers the whole body before dispatch, so the parse
    # runs at disk speed as one contiguous burst. Measured through this
    # endpoint, a multipart part costs ~1.15 ms/MB and a JSON body ~3 ms/MB
    # -- seconds of CPU per GB, per anonymous request, at 120 a minute.
    #
    # Eight times MAX_CONFIG_LENGTH. The two are in different units, and
    # the encoding sits between them: a config at its limit can arrive
    # percent-encoded (3x worst case) or JSON-escaped (6x, ``\uXXXX``), so
    # a tighter bound here would make the config bound unreachable and
    # answer the wrong refusal for a config that is legal.
    MAX_BODY_LENGTH: ClassVar[int] = 8 * 64 * 1024

    # Django's default request-body limit is 2.5 MB, four orders of
    # magnitude past anything a pipeline config needs -- the largest config
    # in this repo is ~3 KB (32 annotators). 64 KiB leaves twenty times that
    # headroom while keeping the parser's input small.
    MAX_CONFIG_LENGTH: ClassVar[int] = 64 * 1024

    # A size bound alone does not bound the *work*: each wildcard annotator
    # scans every resource in the GRR, so a body of many short, individually
    # legal wildcards costs the same as one long query. This caps how many
    # such scans one request can ask for. Three times the largest config in
    # this repo (32 annotators).
    MAX_ANNOTATORS: ClassVar[int] = 100

    # ...and a *declared* count does not bound the work either, because a
    # wildcard is one declaration that becomes up to ``WILDCARD_LIMIT``
    # (500) annotators, every one of which then gets built against the GRR.
    # 100 declared wildcards against a production GRR is therefore up to
    # 50 000 annotator builds from a 3 KB body. Bound what the config
    # expands to, not what it says.
    #
    # The limit is WILDCARD_LIMIT itself: a single maximal wildcard is a
    # legitimate pipeline and must still validate, and nothing larger than
    # one of those is a pipeline anyone edits by hand.
    MAX_EXPANDED_ANNOTATORS: ClassVar[int] = 500

    # The bounds above are per-request; this one is aggregate. The validation
    # pool bounds its WORKERS, not its queue -- a stdlib ``ThreadPoolExecutor``
    # feeds from an unbounded ``SimpleQueue`` -- so work that does not fit in
    # the eight workers simply waits, and under sustained load the wait grows
    # without limit until every request, the editor's own included, hits its
    # client timeout. A queue nobody will still be listening to when it drains
    # is worse than a refusal, so refuse.
    #
    # ``VALIDATE_EXECUTOR.size()`` counts RUNNING + QUEUED tasks, and one
    # request makes up to three trips through that queue (the declared-
    # annotator count, the expansion parse, the build). So this is roughly
    # eight requests in flight, not twenty-four -- three pool-widths of work,
    # about two turns of the queue behind whatever is running.
    #
    # ``AnnotationMixin.VALIDATE_POOL_WORKERS`` is spelled out because a class
    # body cannot see inherited names: ``VALIDATE_POOL_WORKERS`` alone is a
    # NameError here, not an attribute lookup.
    MAX_VALIDATIONS_IN_FLIGHT: ClassVar[int] = (
        3 * AnnotationMixin.VALIDATE_POOL_WORKERS
    )

    # Advertised on the 503. A hint, NOT a prediction -- it is a fixed number
    # and the wait it describes is not.
    #
    # Measured against the containerised stack: with builds fast enough that
    # nothing accumulates, a saturated pool is serving again inside two
    # seconds, and a second is about right. With a slow build (5 s injected,
    # 80 concurrent) the same shed took 63 s to clear -- and that is the
    # regime in which shedding actually happens, so the honest reading is
    # that this under-promises exactly when it matters.
    #
    # Kept at a second deliberately. Being wrong here is cheap in the
    # direction it is wrong: the refusal is decided from `size()` before any
    # pool work, so a client that takes the hint literally and retries buys
    # another refusal for almost nothing, and the editor revalidates on the
    # next keystroke regardless. Deriving it from queue depth would need a
    # per-task cost this endpoint does not measure; #666 is where the cost
    # itself gets addressed.
    SHED_RETRY_AFTER_SECONDS: ClassVar[int] = 1

    @classmethod
    def _refuse_unbounded_body(cls, request: Request) -> Response | None:
        """Refuse on the declared body size, before the body is parsed.

        Reads ``CONTENT_LENGTH`` rather than the body itself, which is what
        makes this the one refusal on the endpoint that costs nothing: it
        decides from a header, without materialising or parsing anything.

        A missing or unparseable length is refused too. The header is the
        only thing being trusted here, so a request that omits it -- as
        chunked transfer encoding does -- would otherwise opt out of the
        bound entirely, on the one route that is anonymous. Nothing that
        calls this endpoint streams: the editor posts a small body with a
        length, and so does every test. Refusing is the cheap side of that
        trade.

        Returns the refusal, or ``None`` to continue.
        """
        declared = request.META.get("CONTENT_LENGTH")
        try:
            length = int(declared)
        except (TypeError, ValueError):
            return Response(
                {"error": "request body must declare its length"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        if length > cls.MAX_BODY_LENGTH:
            return Response(
                {"error": (
                    f"request body is too large: {length} bytes, at most "
                    f"{cls.MAX_BODY_LENGTH} are accepted"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        return None

    @classmethod
    def _shed_when_saturated(cls) -> Response | None:
        """Refuse with 503 when the validation pool is already full.

        Admission control, not a per-request bound: it refuses a *legal*
        request because of what everyone else is asking for. The value it
        adds over queueing is honesty -- the caller learns immediately that
        the work was not attempted, and ``Retry-After`` says the same request
        will be served once the backlog drains.

        Returns the refusal, or ``None`` to continue.
        """
        in_flight = cls.VALIDATE_EXECUTOR.size()
        if in_flight < cls.MAX_VALIDATIONS_IN_FLIGHT:
            return None

        logger.warning(
            "shedding a validation request: %d tasks in flight, bound is %d",
            in_flight, cls.MAX_VALIDATIONS_IN_FLIGHT,
        )
        return Response(
            {"error": "too many validations in flight; retry shortly"},
            status=views.status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": str(cls.SHED_RETRY_AFTER_SECONDS)},
        )

    async def post(self, request: Request) -> Response:
        """Validate annotation config.

        Inline is not free on an async view: this coroutine runs ON the
        event loop, where slow work is not a slow request but a stalled
        server, and unlike a busy thread the loop cannot be preempted. So
        what runs inline here is only what is bounded small, and everything
        whose cost follows untrusted input is awaited on the bounded
        validation pool.

        Inline:

        - The declared-length refusal (#676), which reads a header and
          touches no body.
        - The body parse. It is the widest untrusted input the API accepts,
          but #676 caps it at ``MAX_BODY_LENGTH`` before the parser sees
          it, and a bounded parse is not a long pole -- ~3 ms/MB for JSON
          against a 512 KiB ceiling. Without that bound this would belong
          on the pool with the rest.
        - The ``MAX_CONFIG_LENGTH`` refusal, O(1) on an already-
          materialised string.
        - The admission check, which reads one integer off the pool.

        Awaited on the pool:

        - The declared-annotator count needs a ``yaml.safe_load`` of the
          whole config, and at the size ``MAX_CONFIG_LENGTH`` permits that
          is not cheap, because yaml's cost per byte follows the shape and
          not just the size: 555 ms for ``"- [1,2,3,4,5]\\n"`` x 4681
          (65 534 chars), 473 ms for a 62 KiB flow sequence of mappings,
          against 6 ms for a hand-written config at ``MAX_ANNOTATORS``.
          Submitted even though it gates a *refusal*: a refused request
          costs a worker slot for one parse, which is the price of not
          paying it out of the loop.
        - The expansion-gate parse resolves nothing but scans the whole
          repository once per wildcard: measured against a production-scale
          GRR (grr_encode, 7922 position scores), 27 ms for one wildcard and
          1.59 s for a config at ``MAX_ANNOTATORS``.
        - The build resolves every resource and builds every annotator.

        The ordering is unchanged from the synchronous view: every bound and
        the expansion gate still refuse *before* the build is submitted.
        """

        oversized = self._refuse_unbounded_body(request)
        if oversized is not None:
            return oversized

        content = request.data.get("config") \
            if isinstance(request.data, dict) else None
        if not isinstance(content, str):
            # Both of these were `assert`s, so a body that was not a JSON
            # object, or was one without a `config`, raised AssertionError
            # -- a 500 on an anonymous endpoint, buyable with three bytes.
            return Response(
                {"error": "config is required and must be a string"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        if len(content) > self.MAX_CONFIG_LENGTH:
            return Response(
                {"error": (
                    f"annotation config is too long: {len(content)} "
                    f"characters, at most {self.MAX_CONFIG_LENGTH} "
                    f"are accepted"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        # The memo (#833). It sits after every bound that refuses from the
        # request alone -- so a malformed request still gets its own accurate
        # 400 -- and *before* the admission check, so a hit is neither
        # charged a pool slot nor shed. Nothing else could be true of it: a
        # hit does no work, and refusing a request because the pool is full
        # when the answer is already in hand would shed for nothing.
        memoised = self.validation_cache.get(content, self.grr)
        if memoised is not None:
            return self._verdict_response(memoised)

        # Admission control, checked exactly once, here: after every bound
        # that can refuse from the request alone, and immediately before the
        # first pool submission. Earlier would answer 503 to a request that
        # has its own accurate 400 waiting -- a malformed body deserves to be
        # told it is malformed, however busy the server is, and that refusal
        # costs nothing to produce. Later would be pointless: by then the
        # queueing this exists to avoid has already happened.
        #
        # No lock. From the check to the submission below there is no `await`
        # -- this is one synchronous block on a single event loop -- and
        # VALIDATE_EXECUTOR has exactly one user, this view. Nothing can
        # interleave between reading the size and adding to it, so a lock
        # would guard against a race that cannot occur.
        shed = self._shed_when_saturated()
        if shed is not None:
            return shed

        annotator_count = await self._acount_annotators(content)
        if annotator_count is not None \
                and annotator_count > self.MAX_ANNOTATORS:
            return Response(
                {"error": (
                    f"annotation config declares too many annotators: "
                    f"{annotator_count}, at most {self.MAX_ANNOTATORS} "
                    f"are accepted"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Parsing expands the wildcards but builds nothing, so the
            # expanded count is knowable before paying for it. This is why
            # the parse is kept separate from `load_pipeline_from_yaml`
            # rather than letting the latter's own parse do the work twice:
            # it is the gate on the build that follows.
            _, expanded = await self._aparse_config(content)
        except Exception as e:  # noqa: BLE001
            # Same formatter as the deferred-load failure path (#155) so the
            # synchronous and background error messages stay identical.
            return self._memoise(content, format_config_error(e))

        if len(expanded) > self.MAX_EXPANDED_ANNOTATORS:
            return Response(
                {"error": (
                    f"annotation config expands to too many annotators: "
                    f"{len(expanded)}, at most "
                    f"{self.MAX_EXPANDED_ANNOTATORS} are accepted"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        errors = ""

        try:
            # The long pole: resolving every resource and building every
            # annotator. Awaited off the request thread, so neither the
            # shared thread_sensitive sync-view thread nor the event loop
            # is occupied while it runs.
            await self._abuild_pipeline(content)
        except Exception as e:  # noqa: BLE001
            # Failures arrive from the worker thread through the future, so
            # the same formatter as the deferred-load path (#155) still
            # renders them -- identical text to the synchronous build.
            errors = format_config_error(e)

        return self._memoise(content, errors)

    @staticmethod
    def _verdict_response(errors: str) -> Response:
        """Render a verdict as this endpoint's 200.

        The one place a 200 leaves this view -- the memo hit and the two
        computed outcomes all come through here -- so the shape the editor
        reads (``errors``, empty when the config builds) is stated once.
        """
        return Response({"errors": errors}, status=views.status.HTTP_200_OK)

    def _memoise(self, content: str, errors: str) -> Response:
        """Remember a freshly computed verdict, and render it (#833).

        The one place a verdict *enters* the memo, which is why it is
        separate from ``_verdict_response``: a memo hit renders without
        storing, and had one helper done both, the hit path would have had to
        re-implement the rendering.

        Both computed outcomes are memoised -- a config that does not build
        has a verdict too, and the editor re-posts a broken config as
        insistently as a working one.

        The refusals deliberately do not come through here, because a memo
        entry is a 200 and turning a refusal into one on its second send
        would be a contract change. A 503 is a property of the moment, and
        three of the four 400s are properties of the request alone -- its
        declared length, its body shape, how many annotators the config
        *declares* -- all cheap to re-derive.

        The fourth, ``MAX_EXPANDED_ANNOTATORS``, is not: what a wildcard
        expands to is a property of the repository. So the verdict of a
        config that expands to just under the bound is TTL-bounded like any
        other -- if the repository grew past it, the memo would keep saying
        200 until the entry expired. Not reachable today (a live process
        never re-reads the repository; see
        ``docs/833-validate-result-memo.md``), and inside the staleness bound
        this endpoint documents either way.
        """
        self.validation_cache.put(content, errors, self.grr)
        return self._verdict_response(errors)

    @staticmethod
    async def _await_cancellable(future: Future[_T]) -> _T:
        """Await a per-request pool task, cancelling it if the caller goes.

        ``await_build`` on its own deliberately leaves the submitted future
        running when the awaiting task is cancelled, because it is written
        for *shared* builds: several callers can await one cached build, and
        one of them navigating away must not cancel the build the others are
        still waiting on.

        Nothing this view submits is shared. Each task belongs to exactly one
        request, so once that request is gone the task has no consumer at
        all -- and the editor's debounce makes abandoned requests the normal
        case, not an edge one. Left queued, they occupy a bounded pool with
        work nobody will read while live requests wait behind them.

        Only a task that has not started can be stopped -- ``cancel()`` is a
        no-op once a worker has picked it up, and Python cannot interrupt a
        running thread. Queued-and-superseded is the case this addresses, and
        it is the one a debounced editor produces in bulk.
        """
        try:
            return await await_build(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def _acount_annotators(self, content: str) -> int | None:
        """Await the declared-annotator count on the bounded validation pool.

        The count itself is trivial; the ``yaml.safe_load`` under it is not,
        because it runs on input bounded only by ``MAX_CONFIG_LENGTH`` and
        yaml's cost per byte depends on the shape, not just the size (see
        the numbers on ``post``). It resolves no resource, so unlike the
        expansion parse it does not scale with the repository -- but half a
        second is half a second, and on the event loop it is half a second
        of the whole process.

        Submitted rather than run inline even though it gates a *refusal*:
        a refused request now costs a worker slot for the length of one
        parse, which is the price of not paying it out of the loop.

        What moving it cannot buy back is the GIL the parse holds while it
        runs; only a smaller body bound would (#635 set that deliberately,
        and this is not where it is reopened). The measured residual is
        recorded in ``docs/659-validate-async-slo.md``.
        """
        return await self._await_cancellable(
            self.VALIDATE_EXECUTOR.execute(_count_annotators, content=content),
        )

    async def _aparse_config(
        self, content: str,
    ) -> tuple[AnnotationPreamble | None, list[AnnotatorInfo]]:
        """Await the expansion-gate parse on the bounded validation pool.

        The parse resolves no resource, but every wildcard it expands scans
        the whole repository, so its cost is the repository's size times the
        number of wildcards the body declares -- work that must not run on
        the event loop this coroutine is scheduled on.

        Raises whatever the parse raises, from the worker thread through the
        future, so the caller's ``format_config_error`` sees the same
        exception it saw when the parse ran inline.
        """
        return await self._await_cancellable(
            self.VALIDATE_EXECUTOR.execute(
                AnnotationConfigParser.parse_str,
                content=content, grr=self.grr,
            ),
        )

    async def _abuild_pipeline(self, content: str) -> None:
        """Await the validation build on the bounded validation pool.

        The build belongs to one request and nothing else awaits it, so if
        that request goes away the task is cancelled rather than left to
        occupy a worker for a result no one will read -- see
        ``_await_cancellable``. A build already running finishes; only a
        queued one can be stopped.
        """
        await self._await_cancellable(
            self.VALIDATE_EXECUTOR.execute(
                self._build_pipeline, content=content, grr=self.grr,
            ),
        )

    @staticmethod
    def _build_pipeline(content: str, grr: GenomicResourceRepo) -> None:
        """Build the pipeline for validation, on a worker thread.

        The built pipeline is deliberately dropped: validation only cares
        whether the build raises. That is also why the memo (#833) stores a
        string rather than this -- the verdict is the whole of what the
        endpoint needs, and it is what makes an entry small enough for a
        bound on the entry count to be a bound on memory.
        """
        # LOAD-TEST AID (iossifovlab/gain#164, extended here by #659): the
        # same env-gated, defaults-to-0.0 delay the cache's loader applies.
        # This endpoint never goes through the cache, so without it the
        # harness has no way to induce a slow *validation* build. A true
        # no-op unless GPFWA_BUILD_DELAY_SECONDS is set, and applied on the
        # worker thread -- where a slow real GRR build would block.
        build_delay = _load_test_build_delay()
        if build_delay > 0.0:
            time.sleep(build_delay)
        load_pipeline_from_yaml(content, grr)


class LoadPipeline(AnnotationBaseView):
    """Validate annotation config."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def post(self, request: Request) -> Response:
        """Validate annotation config."""
        assert isinstance(request.data, dict)

        pipeline_id = request.data.get("id")
        if not pipeline_id:
            return Response(
                {"reason": "Pipeline ID not provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        self.put_pipeline(
            pipeline_id,
            request.user,
        )

        return Response(status=views.status.HTTP_204_NO_CONTENT)
