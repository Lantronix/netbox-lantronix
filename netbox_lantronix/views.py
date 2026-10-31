from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import View
from netbox.views import generic
from utilities.views import register_model_view

from . import filtersets, forms, tables
from .jobs import SyncJob, sync_is_active
from .models import (
    DeviceLink,
    DeviceTypeOverride,
    LinkStatusChoices,
    ModelRule,
    PercepxionSource,
    SyncFinding,
)

SYNC_PERM = "netbox_lantronix.sync_percepxionsource"


#
# Sources
#


@register_model_view(PercepxionSource, "list", path="", detail=False)
class PercepxionSourceListView(generic.ObjectListView):
    queryset = PercepxionSource.objects.annotate(links__count=Count("links"))
    table = tables.PercepxionSourceTable
    filterset = filtersets.PercepxionSourceFilterSet
    filterset_form = forms.PercepxionSourceFilterForm


@register_model_view(PercepxionSource)
class PercepxionSourceView(generic.ObjectView):
    queryset = PercepxionSource.objects.all()

    def get_extra_context(self, request, instance):
        last_job = SyncJob.get_jobs(instance).exclude(started=None).order_by("-started").first()
        findings = SyncFinding.objects.restrict(request.user, "view").filter(source=instance)
        if last_job:
            findings = findings.filter(job=last_job)
        cfg = instance.endpoint_config or {}
        findings_table = tables.SyncFindingTable(findings[:200])
        findings_table.configure(request)
        return {
            "last_job": last_job,
            "next_job": SyncJob.get_jobs(instance)
            .filter(started=None)
            .order_by("scheduled")
            .first(),
            "findings_table": findings_table,
            "tls_off": cfg.get("verify", True) is False,
            "can_sync": request.user.has_perm(SYNC_PERM, instance),
        }


@register_model_view(PercepxionSource, "add", detail=False)
@register_model_view(PercepxionSource, "edit")
class PercepxionSourceEditView(generic.ObjectEditView):
    queryset = PercepxionSource.objects.all()
    form = forms.PercepxionSourceForm


@register_model_view(PercepxionSource, "delete")
class PercepxionSourceDeleteView(generic.ObjectDeleteView):
    queryset = PercepxionSource.objects.all()


@register_model_view(PercepxionSource, "sync", path="sync")
class PercepxionSourceSyncView(View):
    """POST only: start a sync now."""

    def post(self, request, pk):
        source = get_object_or_404(PercepxionSource.objects.restrict(request.user, "view"), pk=pk)
        if not request.user.has_perm(SYNC_PERM, source):
            messages.error(request, "You don't have permission to sync this source.")
        elif not source.enabled:
            messages.warning(request, "This source is disabled.")
        elif sync_is_active(source):
            messages.warning(request, "A sync for this source is already queued or running.")
        else:
            job = SyncJob.enqueue(instance=source, user=request.user, manual=True)
            messages.success(request, f"Sync queued (job {job.pk}).")
        return redirect(source.get_absolute_url())


#
# Model rules and overrides
#


@register_model_view(ModelRule, "list", path="", detail=False)
class ModelRuleListView(generic.ObjectListView):
    queryset = ModelRule.objects.select_related("source", "device_type")
    table = tables.ModelRuleTable
    filterset = filtersets.ModelRuleFilterSet


@register_model_view(ModelRule)
class ModelRuleView(generic.ObjectView):
    queryset = ModelRule.objects.all()


@register_model_view(ModelRule, "add", detail=False)
@register_model_view(ModelRule, "edit")
class ModelRuleEditView(generic.ObjectEditView):
    queryset = ModelRule.objects.all()
    form = forms.ModelRuleForm


@register_model_view(ModelRule, "delete")
class ModelRuleDeleteView(generic.ObjectDeleteView):
    queryset = ModelRule.objects.all()


@register_model_view(DeviceTypeOverride, "list", path="", detail=False)
class DeviceTypeOverrideListView(generic.ObjectListView):
    queryset = DeviceTypeOverride.objects.select_related("source", "device_type")
    table = tables.DeviceTypeOverrideTable
    filterset = filtersets.DeviceTypeOverrideFilterSet


@register_model_view(DeviceTypeOverride)
class DeviceTypeOverrideView(generic.ObjectView):
    queryset = DeviceTypeOverride.objects.all()


@register_model_view(DeviceTypeOverride, "add", detail=False)
@register_model_view(DeviceTypeOverride, "edit")
class DeviceTypeOverrideEditView(generic.ObjectEditView):
    queryset = DeviceTypeOverride.objects.all()
    form = forms.DeviceTypeOverrideForm


@register_model_view(DeviceTypeOverride, "delete")
class DeviceTypeOverrideDeleteView(generic.ObjectDeleteView):
    queryset = DeviceTypeOverride.objects.all()


#
# Device links
#


@register_model_view(DeviceLink, "list", path="", detail=False)
class DeviceLinkListView(generic.ObjectListView):
    queryset = DeviceLink.objects.select_related("source", "device")
    table = tables.DeviceLinkTable
    filterset = filtersets.DeviceLinkFilterSet


@register_model_view(DeviceLink)
class DeviceLinkView(generic.ObjectView):
    queryset = DeviceLink.objects.all()

    def get_extra_context(self, request, instance):
        return {
            "can_approve": request.user.has_perm("netbox_lantronix.approve_devicelink", instance)
        }


@register_model_view(DeviceLink, "delete")
class DeviceLinkDeleteView(generic.ObjectDeleteView):
    queryset = DeviceLink.objects.all()


@register_model_view(DeviceLink, "approve", path="approve")
class DeviceLinkApproveView(View):
    """POST only: approve a pending adoption."""

    def post(self, request, pk):
        link = get_object_or_404(DeviceLink.objects.restrict(request.user, "view"), pk=pk)
        if not request.user.has_perm("netbox_lantronix.approve_devicelink", link):
            messages.error(request, "You don't have permission to approve device links.")
        elif link.status != LinkStatusChoices.PENDING:
            messages.info(request, "This link is already active.")
        else:
            link.snapshot()
            link.status = LinkStatusChoices.ACTIVE
            link.save()
            messages.success(request, f"Linked {link.device}. The next sync updates it.")
        return redirect(link.get_absolute_url())


#
# Findings
#


@register_model_view(SyncFinding, "list", path="", detail=False)
class SyncFindingListView(generic.ObjectListView):
    queryset = SyncFinding.objects.select_related("source")
    table = tables.SyncFindingTable
    filterset = filtersets.SyncFindingFilterSet
    actions = {}
