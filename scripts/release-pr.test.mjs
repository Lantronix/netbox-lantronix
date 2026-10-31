import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bumpFor, dependabotItem, nextVersion, notesFor, plan, readVersion, writeVersion } from './release-pr.mjs'

const URL = 'https://github.com/example/repo'
const log = (unreleased) => `# Changelog

## [Unreleased]
${unreleased}
## [1.1.0] - 2026-10-31

### Added
- First feature.

[Unreleased]: ${URL}/compare/v1.1.0...HEAD
[1.1.0]: ${URL}/compare/v1.0.0...v1.1.0
`

test('bump comes from the Unreleased headings', () => {
  assert.equal(bumpFor(['Breaking', 'Added']), 'major')
  assert.equal(bumpFor(['Added', 'Fixed']), 'minor')
  assert.equal(bumpFor(['Removed']), 'minor')
  assert.equal(bumpFor(['Fixed', 'Security']), 'patch')
  assert.equal(bumpFor([]), null)
  assert.equal(nextVersion('1.1.0', 'major'), '2.0.0')
  assert.equal(nextVersion('1.1.0', 'minor'), '1.2.0')
  assert.equal(nextVersion('0.1.0', 'patch'), '0.1.1')
})

test('nothing to release when Unreleased is empty and no Dependabot PRs', () => {
  assert.equal(plan(log('\n'), '1.1.0', '2026-11-02', URL), null)
})

test('renames Unreleased, keeps older sections and adds the compare link', () => {
  const r = plan(log('\n### Fixed\n- A fix.\n\n'), '1.1.0', '2026-11-02', URL)
  assert.equal(r.version, '1.1.1')
  assert.match(r.changelog, /## \[Unreleased\]\n\n## \[1\.1\.1\] - 2026-11-02\n\n### Fixed\n- A fix\.\n\n## \[1\.1\.0\]/)
  assert.match(r.changelog, /\[Unreleased\]: .*compare\/v1\.1\.1\.\.\.HEAD\n\[1\.1\.1\]: .*compare\/v1\.1\.0\.\.\.v1\.1\.1\n\[1\.1\.0\]/)
})

test('first release adds the link block when there is none', () => {
  const r = plan('# Changelog\n\n## [Unreleased]\n\n### Added\n- Everything.\n', '0.1.0', '2026-11-02', URL, [], {}, false)
  assert.equal(r.version, '0.1.0', 'first release ships the declared version')
  assert.match(r.changelog, /\[Unreleased\]: .*compare\/v0\.1\.0\.\.\.HEAD\n\[0\.1\.0\]: .*releases\/tag\/v0\.1\.0\n$/)
})

test('a repo with no remote gets no link lines', () => {
  const r = plan('# Changelog\n\n## [Unreleased]\n\n### Added\n- Everything.\n', '0.1.0', '2026-11-02', '', [], {}, false)
  assert.equal(r.version, '0.1.0')
  assert.doesNotMatch(r.changelog, /^\[/m)
})

test('Dependabot PRs alone make a patch release, and mentioned ones are skipped', () => {
  const prs = [
    { number: 31, title: 'chore(deps): Bump hono from 4.13.14 to 4.13.15 in /server' },
    { number: 32, title: 'chore(deps-dev): Bump vitest from 3.1.0 to 3.1.1' },
  ]
  const r = plan(log('\n'), '1.1.0', '2026-11-02', URL, prs, { '/server': 'api' })
  assert.equal(r.version, '1.1.1')
  assert.match(r.section, /^### Changed\n- Dependency updates \(Dependabot\): hono 4\.13\.15 \(api\) \(#31\), vitest 3\.1\.1 \(#32\)\.$/)
  const mentioned = plan(log('\n### Changed\n- hono 4.13.15, by hand.\n\n'), '1.1.0', '2026-11-02', URL, prs)
  assert.doesNotMatch(mentioned.section, /hono 4\.13\.15 \(server\)/)
  assert.match(mentioned.section, /vitest 3\.1\.1 \(#32\)/)
  assert.equal(dependabotItem('chore(deps-dev): Bump the lint group across 1 directory with 5 updates').key, null)
})

test('release notes are the version section without link lines', () => {
  assert.equal(notesFor(log('\n'), '1.1.0'), '### Added\n- First feature.')
  assert.equal(notesFor(log('\n'), '9.9.9'), null)
})

test('reads and writes versions in each file type', () => {
  const pkg = '{\n  "name": "x",\n  "version": "1.0.0",\n  "private": true\n}\n'
  assert.equal(readVersion('package.json', pkg), '1.0.0')
  assert.equal(writeVersion('package.json', pkg, '1.1.0'), '{\n  "name": "x",\n  "version": "1.1.0",\n  "private": true\n}\n')
  const lock = JSON.stringify({ name: 'x', version: '1.0.0', packages: { '': { version: '1.0.0' }, 'node_modules/a': { version: '1.0.0' } } }, null, 2)
  const l = JSON.parse(writeVersion('sub/package-lock.json', lock, '1.1.0'))
  assert.equal(l.version, '1.1.0')
  assert.equal(l.packages[''].version, '1.1.0')
  assert.equal(l.packages['node_modules/a'].version, '1.0.0')
  const py = '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "x"\nversion = "0.1.0"\n\n[tool.ruff]\nversion = "nope"\n'
  assert.equal(readVersion('pyproject.toml', py), '0.1.0')
  assert.equal(writeVersion('pyproject.toml', py, '0.2.0'), py.replace('version = "0.1.0"', 'version = "0.2.0"'))
  assert.equal(writeVersion('VERSION', '0.1.0\n', '0.1.1'), '0.1.1\n')
  const cargo = '[package]\nname = "x"\nversion = "0.3.0"\n\n[dependencies]\nserde = { version = "1" }\n'
  assert.equal(readVersion('Cargo.toml', cargo), '0.3.0')
  const pyfile = 'try:\n    __version__ = metadata.version("x")\nexcept Error:\n    __version__ = "0.1.0"\nCLIENT_VERSION = "0.1.0"\nmin_version = "4.5.0"\nAPI_VERSION = "2.0.0"\n'
  assert.equal(readVersion('pkg/__init__.py', pyfile), '0.1.0')
  const written = writeVersion('pkg/__init__.py', pyfile, '0.2.0')
  assert.match(written, /__version__ = "0\.2\.0"\nCLIENT_VERSION = "0\.2\.0"\nmin_version = "4\.5\.0"\nAPI_VERSION = "2\.0\.0"/)
})
