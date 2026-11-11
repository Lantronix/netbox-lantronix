from django.shortcuts import get_object_or_404
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .. import filtersets
from ..jobs import SyncJob, sync_is_active
from ..models import DeviceLink, DeviceTypeOverride, ModelRule, PercepxionSource
from ..views import SYNC_PERM, links_for
from . import serializers


class CanRunSync(permissions.BasePermission):
    """Signed in, and a write-enabled token if one is used. The sync permission is checked in the
    action itself, so this replaces the model-permission check that maps POST to "add"."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        token = request.auth
        return getattr(token, "write_enabled", True) if token is not None else True


class PercepxionSourceViewSet(NetBoxModelViewSet):
    queryset = PercepxionSource.objects.all()
    serializer_class = serializers.PercepxionSourceSerializer
    filterset_class = filtersets.PercepxionSourceFilterSet

    def get_permissions(self):
        if self.action == "sync":
            return [CanRunSync()]
        return super().get_permissions()

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """Queue a sync now, as the "Sync now" button does.

        For event-driven syncs: a webhook receiver calls this when Percepxion reports a port or
        device change. Answers 202 with the job, or 409 when the source is disabled or a sync is
        already queued or running (the change will be picked up by that run).
        """
        source = get_object_or_404(PercepxionSource.objects.restrict(request.user, "view"), pk=pk)
        if not request.user.has_perm(SYNC_PERM, source):
            return Response(
                {"detail": "You don't have permission to sync this source."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not source.enabled:
            return Response({"detail": "This source is disabled."}, status=status.HTTP_409_CONFLICT)
        if sync_is_active(source):
            return Response(
                {"detail": "A sync for this source is already queued or running."},
                status=status.HTTP_409_CONFLICT,
            )
        job = SyncJob.enqueue(instance=source, user=request.user, manual=True)
        return Response(
            {"job": {"id": job.pk, "url": job.get_absolute_url()}}, status=status.HTTP_202_ACCEPTED
        )


class DeviceLinkViewSet(NetBoxModelViewSet):
    # Links are written by the sync; the API reads and deletes them.
    http_method_names = ["get", "head", "options", "delete"]
    queryset = DeviceLink.objects.all()
    serializer_class = serializers.DeviceLinkSerializer
    filterset_class = filtersets.DeviceLinkFilterSet

    def get_queryset(self):
        return links_for(self.request.user, super().get_queryset())


class ModelRuleViewSet(NetBoxModelViewSet):
    queryset = ModelRule.objects.all()
    serializer_class = serializers.ModelRuleSerializer
    filterset_class = filtersets.ModelRuleFilterSet


class DeviceTypeOverrideViewSet(NetBoxModelViewSet):
    queryset = DeviceTypeOverride.objects.all()
    serializer_class = serializers.DeviceTypeOverrideSerializer
    filterset_class = filtersets.DeviceTypeOverrideFilterSet
