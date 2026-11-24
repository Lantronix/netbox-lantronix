import logging

from django.shortcuts import get_object_or_404
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import filtersets
from ..endpoint_checks import webhook_secrets
from ..jobs import SyncJob, request_sync, sync_is_active, sync_requests
from ..models import DeviceLink, DeviceTypeOverride, ModelRule, PercepxionSource
from ..percepxion.events import MAX_BODY, EventRejected, parse, verify
from ..sanitize import safe_id
from ..views import SYNC_PERM, links_for
from . import serializers

log = logging.getLogger("netbox_lantronix.events")
MAX_DEVICE_IDS = 500


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

        With no body: a full sync. Answers 202 with the job, or 409 when the source is disabled
        or a sync is already queued or running.

        With ``{"device_ids": [...]}`` (Percepxion device IDs): a focused sync of those console
        servers, merged with any focused sync already waiting. Answers 202 even while another
        sync runs; the focused sync runs after it.
        """
        source = get_object_or_404(PercepxionSource.objects.restrict(request.user, "view"), pk=pk)
        if not request.user.has_perm(SYNC_PERM, source):
            return Response(
                {"detail": "You don't have permission to sync this source."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not source.enabled:
            return Response({"detail": "This source is disabled."}, status=status.HTTP_409_CONFLICT)
        data = request.data if isinstance(request.data, dict) else {}
        if "device_ids" in data:
            ids = data["device_ids"]
            if (
                not isinstance(ids, list)
                or not 0 < len(ids) <= MAX_DEVICE_IDS
                or any(safe_id(i) is None for i in ids)
            ):
                return Response(
                    {"detail": f"device_ids must be a list of 1 to {MAX_DEVICE_IDS} device IDs."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queued = request_sync(source, ids, user=request.user)
            return Response(
                {"focused": True, "devices": len(set(ids)), "new_job": queued},
                status=status.HTTP_202_ACCEPTED,
            )
        if sync_is_active(source):
            return Response(
                {"detail": "A sync for this source is already queued or running."},
                status=status.HTTP_409_CONFLICT,
            )
        job = SyncJob.enqueue(instance=source, user=request.user, manual=True)
        return Response(
            {"job": {"id": job.pk, "url": job.get_absolute_url()}}, status=status.HTTP_202_ACCEPTED
        )


class SourceEventsView(APIView):
    """Signed Percepxion events (webhook-payload v1) for one source.

    No NetBox login or token: the HMAC signature, with a secret from the endpoint's
    ``webhook_secrets``, is the authentication. A verified port or device event for this
    source's organization requests a focused sync of that console server."""

    authentication_classes = ()
    permission_classes = (permissions.AllowAny,)
    parser_classes = ()  # the signature covers the raw body, so it is never parsed here

    def post(self, request, pk):
        source = PercepxionSource.objects.filter(pk=pk, enabled=True).first()
        secrets = webhook_secrets(source.endpoint_config) if source else []
        if not secrets:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return Response({"detail": "Bad body length."}, status=status.HTTP_400_BAD_REQUEST)
        body = request.body
        try:
            verify(secrets, request.headers, body)
        except EventRejected as exc:
            log.warning("Event for source %s refused: %s", source.pk, exc)
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            event = parse(body)
        except EventRejected as exc:
            log.warning("Event for source %s refused: %s", source.pk, exc)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if event is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        if event.organization_id != source.organization_id:
            log.warning("Event for source %s refused: another organization", source.pk)
            return Response(
                {"detail": "Event is for another organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sync_requests(source).seen(event.id):
            return Response(status=status.HTTP_204_NO_CONTENT)
        request_sync(source, [event.device_id])
        return Response({"detail": "Sync requested."}, status=status.HTTP_202_ACCEPTED)


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
