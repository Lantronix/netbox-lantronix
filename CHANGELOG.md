# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Add each user-visible change under **Unreleased** as it merges, under the heading that sets the next version: **Breaking** (a change users must act on) for a major, **Added** or **Removed** for a minor, **Changed**, **Fixed**, **Deprecated** or **Security** for a patch. Dependabot updates are listed at release time. Each release is a `vX.Y.Z` tag with a GitHub Release built from its section below.

## [Unreleased]

## [0.6.1] - 2026-11-11

### Security

- Device links are listed, shown and deleted only for devices the user can view. Before, view permission on device links showed the name, management address and firmware of every linked console server.
- Approving a pending adoption now also needs change permission on the device being adopted.
- A pending adoption is no longer recorded for a device the sync user can't change, and port facts and link details are no longer written for one. Before, these records ignored the sync user's object permissions.
- The **Open console** button isn't shown for a loopback, link-local, multicast or unspecified address, and its tooltip shows the address and port it opens.
- Each sync now revokes its Percepxion refresh token and closes the connection when it has finished reading.
- Conflict findings no longer name the manufacturer or platform of another NetBox device.
- Zero-width and other invisible characters are removed from labels, descriptions and attached-device details read from Percepxion.

### Fixed

- Stored `show sysconfig` output that can't be read no longer stops the sync. The device type falls back to the model rules.
- A mistyped `web_port_url` no longer breaks console port pages; the **Open in Percepxion** button is left out.
- The SSH base port must be between 1 and 65000, so the port a console link opens is always valid.
- The manufacturer, platform and custom field the plugin creates on its first run are now part of the sync's transaction and change log.

## [0.6.0] - 2026-11-11

### Security

- The Percepxion client no longer follows HTTP redirects. A redirect from the endpoint could carry the session token, and on a 307 or 308 the login request, to another host. A redirect now fails the sync with "was redirected".
- Devices outside a source's organization are no longer named in findings, and the plugin no longer asks Percepxion for their port state. A sync records one "out of organization" finding with a count.
- The endpoint setting `verify` must be `True` or `False`. Values such as `None`, `0` or an empty string turned certificate checking off without showing the warning; they now stop the plugin at startup with an error that names the endpoint.

### Added

- Endpoint setting `organizations`: the list of Percepxion organization IDs that sources on the endpoint may sync. Set it when the Percepxion account can see more than one organization. Without it, anyone who can add or edit a source can sync any organization the account can see.

### Fixed

- A refused Percepxion call no longer counts as a rejected login. Before, any HTTP 401 or 403, even for one device, paused the source for at least 30 minutes and blocked **Sync now**. Now only a rejected login does that. A session that ends mid-sync logs in again once, and a device whose port state is refused is skipped.
- A device that NetBox validation rejects no longer fails every sync. It is skipped with a "conflict" finding that gives NetBox's reason, and the other devices sync.
- A Percepxion device whose name differs only by letter case from a NetBox device is now matched to it. Before, the sync tried to create a second device, NetBox refused it, and every run failed.
- The README no longer implies SLC 8000 units get a device type automatically. They need a device override.

## [0.5.4] - 2026-11-10

### Fixed

- A sync with a Percepxion **project_admin** account no longer fails with `POST /v1/job/jobgroup/search returned HTTP 400`. The `show sysconfig` lookups now send the source's organization ID, which Percepxion requires from project_admin sessions.

### Fixed

- A failed or errored sync now writes the source, job number and reason to the NetBox worker log (`docker logs`), not only to the job's own log, so the cause shows up without opening the job.

## [0.5.3] - 2026-11-10

### Fixed
- The plugin's Author field in NetBox's plugin list now shows Lantronix instead of a dash.
- The README's first sync steps now give the real menu path, the Lantronix menu in the sidebar, instead of a Plugins menu that doesn't exist.

## [0.5.2] - 2026-11-10

### Fixed
- The attached device panel on a console server port no longer shows a dash in front of the model or OS version when Percepxion reports no make or OS name.

## [0.5.1] - 2026-11-10

### Changed
- Published on PyPI. Install with `pip install netbox-lantronix`.

## [0.5.0] - 2026-11-10

### Added
- REST endpoint to start a sync: `POST /api/plugins/lantronix/sources/<id>/sync/` queues a run, as the Sync now button does. It answers 202 with the job, 403 without the sync permission, and 409 when the source is disabled or a run is already queued or running. Webhook receivers can call it when Percepxion reports a port or device change, so NetBox updates without waiting for the schedule.

## [0.4.0] - 2026-11-09

### Added
- Device links record the running firmware Percepxion reports, shown on the link and device pages and in the API.

## [0.3.0] - 2026-11-09

### Added
- NetBox 4.7 support.

### Changed
- Requires NetBox 4.6 or later. The plugin's migrations depend on NetBox 4.6, so it never installed on 4.5 even though it allowed 4.5.

## [0.2.1] - 2026-11-09

### Fixed
- NetBox's Plugins page (`/core/plugins/`) and other views that list installed plugins no longer fail with `module 'netbox_lantronix.config' has no attribute 'version'`. The endpoint checks module was named `config`, and loading it replaced the plugin's `config` attribute.

## [0.2.0] - 2026-11-06

### Added
- A Docker quick start in `docker/` that builds a NetBox 4.6 image with the plugin from this repository.
- SLC and EMG device type definitions in `docker/device-types`, with a setup script that imports them and creates the sync user.

### Fixed
- The install steps now use a release archive, and the README's device type steps point to definitions in this repository.

## [0.1.1] - 2026-11-05

### Fixed
- A port whose status is Disconnected now reports nothing attached, even while the SLC still keeps the managed device record, so pulling a console cable shows up as a detach.

## [0.1.0] - 2026-10-31

### Added
- First release. NetBox plugin that keeps NetBox current with the Lantronix SLC 9000, SLC 8000 and EMG console servers managed by Percepxion: devices, console server ports, port labels and attached-device facts.

[Unreleased]: https://github.com/Lantronix/netbox-lantronix/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/Lantronix/netbox-lantronix/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.4...v0.6.0
[0.5.4]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Lantronix/netbox-lantronix/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Lantronix/netbox-lantronix/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Lantronix/netbox-lantronix/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Lantronix/netbox-lantronix/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Lantronix/netbox-lantronix/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Lantronix/netbox-lantronix/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Lantronix/netbox-lantronix/releases/tag/v0.1.0
