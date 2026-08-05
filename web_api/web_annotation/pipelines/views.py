"""Views for pipeline creation and manipulation."""
from pathlib import Path
from typing import ClassVar

import yaml
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpResponse, QueryDict
from gain import logging
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import GenomicResource
from gain.templates import get_template, render_untrusted_markdown
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
from web_annotation.pipeline_cache import ThreadSafePipeline
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
            markdown=render_untrusted_markdown,
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


class PipelineValidation(AnnotationBaseView):
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

    What is deliberately NOT bounded here is the cost of building the
    annotators that survive all three. This endpoint resolves resources and
    builds the pipeline synchronously, on the request thread, which the
    sibling save-pipeline path avoids by deferring to a background loader
    (the #150 H1 comment on ``UserPipeline``). Moving this one likewise is
    the real fix for the residual cost and is tracked separately; the
    bounds here keep the residual small rather than remove it.
    """

    throttle_classes: ClassVar = [PipelineValidationRateThrottle]

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

    def post(self, request: Request) -> Response:
        """Validate annotation config."""

        # Ahead of `request.data` on purpose -- reading it is the parse
        # this refuses (#676).
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

        annotator_count = _count_annotators(content)
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
            _, expanded = AnnotationConfigParser.parse_str(
                content, grr=self.grr)
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
            load_pipeline_from_yaml(content, self.grr)
        except Exception as e:  # noqa: BLE001
            result = {"errors": format_config_error(e)}

        return Response(result, status=views.status.HTTP_200_OK)


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
