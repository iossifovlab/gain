"""Views for pipeline creation and manipulation."""
import time
from pathlib import Path
from typing import Any, ClassVar

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
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.templates import get_template
from markdown2 import markdown
from rest_framework import views
from rest_framework.request import MultiValueDict
from rest_framework.views import Request, Response

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
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
    ThreadSafePipeline,
    _load_test_build_delay,
    await_build,
)
from web_annotation.pipelines.throttling import PipelineValidationRateThrottle

logger = logging.getLogger(__name__)


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
        html_doc = await sync_to_async(self._render_doc)(pipeline)

        response = HttpResponse(html_doc, content_type="text/html")
        response["Content-Disposition"] = (
            f'attachment; filename="{pipeline_id}.html"'
        )
        return response

    @staticmethod
    def _render_doc(pipeline: ThreadSafePipeline) -> str:
        """Render the annotate_doc HTML off the loop (touches GRR metadata)."""
        def make_resource_url(resource: GenomicResource) -> str:
            return resource.get_url()

        def make_histogram_url(
            score: GenomicScore, score_id: str,
        ) -> str | None:
            return score.get_histogram_image_url(score_id)

        template = get_template("annotate_doc_pipeline_template.jinja")
        return template.render(
            pipeline=pipeline,
            pipeline_path=None,
            markdown=markdown,
            res_url=make_resource_url,
            hist_url=make_histogram_url,
        )


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


def _read_request_data(request: Request) -> Any:
    """Parse the request body, off whatever thread the caller submits from.

    ``Request.data`` is a property that parses on first access, so touching
    it *is* the parse. Wrapping it in a named function is what lets it be
    submitted to an executor.
    """
    return request.data


class PipelineValidation(AsyncAnnotationBaseView):
    """Validate annotation config.

    Anonymous and unauthenticated by design -- the pipeline editor validates
    for users who have not signed in. That makes the config body the widest
    piece of untrusted input the API accepts, so it is bounded three times
    on the way in (iossifovlab/gain#635): on its size, on the number of
    annotators it declares, and on the number those annotators expand to
    before any of them is built. Each is a request-level refusal (400), and
    each happens before the work it bounds. A config that is merely
    *invalid* keeps the endpoint's 200 + ``errors`` contract, which the
    deferred-load path (#155) shares.

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

    Whether the endpoint needs a resource-resolving build at all, and
    whether its results should be memoised, is #666 -- not decided here.
    """

    throttle_classes: ClassVar = [PipelineValidationRateThrottle]

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

    async def post(self, request: Request) -> Response:
        """Validate annotation config.

        Nothing here runs inline except the body-size bound, which is O(1)
        on an already-materialised string. Everything else is awaited on the
        bounded validation pool, because inline is not free on an async
        view: this coroutine runs ON the event loop, where slow work is not
        a slow request but a stalled server, and unlike a busy thread the
        loop cannot be preempted.

        - Reading ``request.data`` is the parse of the widest untrusted
          input the API accepts, and nothing on this path bounds it.
          ``DATA_UPLOAD_MAX_MEMORY_SIZE`` is consulted only by
          ``HttpRequest.body``/``.POST``, neither of which DRF's parsers
          use, and for multipart Django bounds only non-file field bytes and
          the file *count* (100) -- never a part's size. Nor does the
          client's upload speed bound it: Django's ASGI handler buffers the
          whole body into a spooled file *before* dispatch, so the parse
          runs at disk speed and lands as one contiguous burst. Measured
          through this endpoint: a multipart file part costs 0.019 s at
          16 MB, 0.147 s at 128 MB and 0.586 s at 512 MB (~1.15 ms/MB),
          and a JSON body ~3 ms/MB -- so ~1-3 s per GB, per anonymous
          request, with the throttle allowing 120 a minute from one IP.
          On the sync view this was a busy worker thread; on the
          loop it would be the whole process going quiet, so it is awaited
          like every other long pole. This is also why the ``MAX_CONFIG_
          LENGTH`` bound cannot be the first thing that runs: the string it
          bounds does not exist until the body is parsed.
        - The declared-annotator count needs a ``yaml.safe_load`` of the
          whole body, and at the size the first bound permits that is not
          cheap: 555 ms for ``"- [1,2,3,4,5]\\n"`` x 4681 (65 534 chars),
          473 ms for a 62 KiB flow sequence of mappings, against 6 ms for a
          hand-written config at ``MAX_ANNOTATORS``. The refusal it
          produces is therefore worth a worker slot -- it is far cheaper
          than half a second of the whole process.
        - The expansion-gate parse resolves nothing but scans the whole
          repository once per wildcard: measured against a production-scale
          GRR (grr_encode, 7922 position scores), 27 ms for one wildcard and
          1.59 s for a config at ``MAX_ANNOTATORS``.
        - The build resolves every resource and builds every annotator.

        The ordering is unchanged: every bound and the expansion gate still
        refuse *before* the build is submitted.
        """

        request_data = await self._aread_body(request)
        content = request_data.get("config") \
            if isinstance(request_data, dict) else None
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
            return Response(
                {"errors": format_config_error(e)},
                status=views.status.HTTP_200_OK,
            )

        if len(expanded) > self.MAX_EXPANDED_ANNOTATORS:
            return Response(
                {"error": (
                    f"annotation config expands to too many annotators: "
                    f"{len(expanded)}, at most "
                    f"{self.MAX_EXPANDED_ANNOTATORS} are accepted"
                )},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        result = {"errors": ""}

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
            result = {"errors": format_config_error(e)}

        return Response(result, status=views.status.HTTP_200_OK)

    async def _aread_body(self, request: Request) -> Any:
        """Await the request-body parse on the bounded validation pool.

        The pool rather than ``sync_to_async``'s default
        ``thread_sensitive=True`` thread, for the reason this issue exists:
        that thread is shared by every synchronous view, and this parse is
        anonymous and size-unbounded (see ``post``). Moving an unbounded
        anonymous parse from the event loop onto the one thread every other
        sync view needs would only relocate the occupancy. The pool is where
        this endpoint's other unbounded work already goes, and it is bounded.

        The parse touches no ORM and no async context -- it reads the
        already-buffered request body -- so it is safe off the
        ``thread_sensitive`` thread.

        Costs a worker slot before the ``MAX_CONFIG_LENGTH`` refusal, which
        is unavoidable: that bound is on a string that does not exist until
        the body is parsed. The refusal is still cheap in the sense that
        matters -- it happens before the count, the expansion gate and the
        build.

        Raises whatever the parse raises (``ParseError``,
        ``UnsupportedMediaType``) from the worker thread through the future,
        i.e. from this ``await``, still inside the handler -- so DRF's
        exception handler renders it exactly as it did when the parse ran
        inline.
        """
        return await await_build(
            self.VALIDATE_EXECUTOR.execute(
                _read_request_data, request=request,
            ),
        )

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
        return await await_build(
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
        return await await_build(
            self.VALIDATE_EXECUTOR.execute(
                AnnotationConfigParser.parse_str,
                content=content, grr=self.grr,
            ),
        )

    async def _abuild_pipeline(self, content: str) -> None:
        """Await the validation build on the bounded validation pool.

        ``await_build`` rather than ``asyncio.wrap_future``: it settles a
        per-request waiter from the submitted future, so cancelling this
        request (a client that navigated away mid-keystroke, which the
        editor's debounce makes routine) cancels only the waiter and leaves
        the running build to finish on its worker.
        """
        await await_build(
            self.VALIDATE_EXECUTOR.execute(
                self._build_pipeline, content=content, grr=self.grr,
            ),
        )

    @staticmethod
    def _build_pipeline(content: str, grr: GenomicResourceRepo) -> None:
        """Build the pipeline for validation, on a worker thread.

        The built pipeline is deliberately dropped: validation only cares
        whether the build raises. (Whether the build is needed at all, and
        whether its verdict should be memoised, is #666.)
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
