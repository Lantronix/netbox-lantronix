# Development environment

1. Start Docker with WSL integration (or any Linux Docker host).
2. Clone netbox-docker to the Linux filesystem and from that directory run:
   ```
   export NETBOX_LANTRONIX_SRC=<path to this repo>
   export PERCEPXION_URL=https://<sandbox api host> PERCEPXION_USERNAME=... PERCEPXION_PASSWORD=...
   cp $NETBOX_LANTRONIX_SRC/dev/netbox-docker/configuration/plugins.py.example \
      $NETBOX_LANTRONIX_SRC/dev/netbox-docker/configuration/plugins.py
   VERSION=v4.6 docker compose -f docker-compose.yml \
     -f $NETBOX_LANTRONIX_SRC/dev/netbox-docker/docker-compose.override.yml up -d
   ```
3. NetBox listens on http://127.0.0.1:8001. Create a superuser and the `percepxion-sync` user:
   `docker compose exec netbox /opt/netbox/netbox/manage.py createsuperuser`
4. Migrations: `docker compose exec netbox /opt/netbox/netbox/manage.py migrate netbox_lantronix`
5. Tests: `docker compose exec netbox /opt/netbox/netbox/manage.py test netbox_lantronix --keepdb`

Never commit `plugins.py`, env files or recorded fixtures that haven't been scrubbed.
