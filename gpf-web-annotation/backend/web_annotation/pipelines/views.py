"""Views for pipeline creation and manipulation."""
import logging
from pathlib import Path
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import QueryDict
from rest_framework import views
from rest_framework.request import MultiValueDict
from rest_framework.views import Request, Response
from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import (
    BaseUser,
    Pipeline,
    TemporaryPipeline,
    WebAnnotationAnonymousUser,
)


logger = logging.getLogger(__name__)


class UserPipeline(AnnotationBaseView):
    """View for saving user annotation pipelines."""

    authentication_classes = [WebAnnotationAuthentication]

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
                {"reason": f"Invalid pipeline configuration file: {str(e)}"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        try:
            AnnotationConfigParser.parse_str(content, grr=self.grr)
            load_pipeline_from_yaml(content, self.grr)
        except (AnnotationConfigurationError, KeyError) as e:
            error = str(e)
            if error == "":
                return Response(
                    {"errors": "Invalid configuration"},
                    status=views.status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"errors": f"Invalid configuration, reason: {error}"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        except Exception:  # pylint: disable=broad-exception-caught
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
            pipeline_name = f'pipeline-{request.user.session_id}.yaml'

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

        config_filename = f'{pipeline_name}.yaml'

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
                if pipeline_name is not None:
                    if Pipeline.objects.filter(
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

        return Response(status=views.status.HTTP_204_NO_CONTENT)


class ListPipelines(AnnotationBaseView):
    """View for listing all annotation pipelines for files."""

    authentication_classes = [WebAnnotationAuthentication]

    def _get_grr_pipelines(self) -> list[dict[str, str]]:
        return [
            {
                "id": pipeline["id"],
                "type": "default",
                "name": pipeline["id"],
                "content": pipeline["content"],
                "status": "loaded" if super().lru_cache.is_pipeline_loaded(
                    pipeline["id"]) else "unloaded",
            }
            for pipeline in self.grr_pipelines.values()
        ]

    def _get_user_pipelines(self, user: BaseUser) -> list[dict[str, str]]:
        pipelines = [
            *user.get_pipelines(),
        ]

        filtered_pipelines = filter(None, pipelines)

        return [
            {
                "id": str(pipeline.pk),
                "name": pipeline.name,
                "type": "user",
                "content": Path(
                    pipeline.config_path
                ).read_text(encoding="utf-8"),
                "status": "loaded" if super().lru_cache.is_pipeline_loaded(
                    pipeline.identifier) else "unloaded",
            }
            for pipeline in filtered_pipelines
        ]

    def get(self, request: Request) -> Response:
        pipelines = self._get_grr_pipelines()
        if request.user and request.user.is_authenticated:
            pipelines = pipelines + self._get_user_pipelines(request.user)

        return Response(
            pipelines,
            status=views.status.HTTP_200_OK,
        )


class PipelineValidation(AnnotationBaseView):
    """Validate annotation config."""

    def post(self, request: Request) -> Response:
        """Validate annotation config."""

        assert request.data is not None
        assert isinstance(request.data, dict)

        content = request.data.get("config")
        assert isinstance(content, str)

        result = {"errors": ""}

        try:
            AnnotationConfigParser.parse_str(content, grr=self.grr)
            load_pipeline_from_yaml(content, self.grr)
        except (AnnotationConfigurationError, KeyError) as e:
            error = str(e)
            if error == "":
                result = {"errors": "Invalid configuration"}
            else:
                result = {"errors": f"Invalid configuration, reason: {error}"}
        except Exception:  # pylint: disable=broad-exception-caught
            result = {"errors": "Invalid configuration"}

        return Response(result, status=views.status.HTTP_200_OK)


class LoadPipeline(AnnotationBaseView):
    """Validate annotation config."""

    authentication_classes = [WebAnnotationAuthentication]

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
