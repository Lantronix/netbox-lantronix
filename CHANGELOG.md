# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Add each user-visible change under **Unreleased** as it merges, under the heading that sets the next version: **Breaking** (a change users must act on) for a major, **Added** or **Removed** for a minor, **Changed**, **Fixed**, **Deprecated** or **Security** for a patch. Dependabot updates are listed at release time. Each release is a `vX.Y.Z` tag with a GitHub Release built from its section below.

## [Unreleased]

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

[Unreleased]: https://github.com/Lantronix/netbox-lantronix/compare/v0.5.3...HEAD
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
