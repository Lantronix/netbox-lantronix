# NetBox image with the netbox-lantronix plugin, built from this checkout.
# Build context is the repository root; see "Docker" in the README.
FROM netboxcommunity/netbox:v4.6-5.0.2

COPY pyproject.toml README.md /tmp/netbox-lantronix/
COPY netbox_lantronix /tmp/netbox-lantronix/netbox_lantronix
RUN /usr/local/bin/uv pip install /tmp/netbox-lantronix && rm -rf /tmp/netbox-lantronix
