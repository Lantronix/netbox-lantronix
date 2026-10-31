"""PortFacts serializes for event rules and webhooks."""

from dcim.models import ConsoleServerPort, Device
from django.test import TestCase
from django.utils import timezone
from extras.events import serialize_for_event
from utilities.api import get_serializer_for_model

from ..api.serializers import PortFactsSerializer
from ..models import PortFacts
from .netbox_fixtures import make_world


class PortFactsSerializerTest(TestCase):
    def setUp(self):
        w = make_world()
        device = Device.objects.create(
            name="SLC-A", site=w["site"], role=w["role"], device_type=w["dtype"]
        )
        self.port = ConsoleServerPort.objects.create(
            device=device, name="Port-14", description="lab-sw1"
        )
        self.facts = PortFacts.objects.create(
            port=self.port,
            make="cisco",
            model="WS-C3560CG-8TC-S",
            state="Managed",
            last_reported=timezone.now(),
        )

    def test_registered_for_events(self):
        self.assertIs(get_serializer_for_model(PortFacts), PortFactsSerializer)

    def test_event_payload_carries_slc_port_and_hostname(self):
        data = serialize_for_event(self.facts)
        self.assertEqual(data["slc"], self.port.device.name)
        self.assertEqual(data["port"]["name"], "Port-14")
        self.assertEqual(data["hostname"], "lab-sw1")
        self.assertEqual(data["model"], "WS-C3560CG-8TC-S")
        self.assertTrue(data["attached"])

    def test_hostname_read_fresh(self):
        ConsoleServerPort.objects.filter(pk=self.port.pk).update(description="renamed")
        self.assertEqual(serialize_for_event(self.facts)["hostname"], "renamed")
