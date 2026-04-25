from typing import ClassVar, cast

from rest_framework import serializers

from .models import AlleleQuery, Job, User


class JobSerializer(serializers.ModelSerializer):
    """Job model serializer class."""
    owner = serializers.ReadOnlyField(source="owner.email")

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for job serializer."""
        model = Job
        fields: ClassVar = [
            "id", "name", "created", "status", "duration", "owner", "error"]

    def to_representation(self, instance: Job) -> dict:
        """Transform status into human-readable format."""
        representation = cast(dict, super().to_representation(instance))
        representation["status"] = Job.Status(instance.status).name.lower()
        return representation


class UserSerializer(serializers.ModelSerializer):
    """User model serializer class."""
    jobs = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Job.objects.all())

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for user serializer."""
        model = User
        fields: ClassVar = ["email", "jobs"]


class AlleleSerializer(serializers.ModelSerializer):
    """Allele model serializer class."""
    owner = serializers.ReadOnlyField(source="owner.email")

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta class for job serializer."""
        model = AlleleQuery
        fields: ClassVar = ["id", "allele", "owner", "note", "last_used"]
