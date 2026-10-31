#!/usr/bin/env node
// Release automation from CHANGELOG.md (Keep a Changelog format). No dependencies.
//
//   node scripts/release-pr.mjs [--date YYYY-MM-DD] [--dependabot prs.json] [--body body.md] [--dry-run]
//       Works out the next version, renames [Unreleased] and writes the
//       version into every file in .github/release.json "versionFiles".
//       Prints the version, or "none" when there's nothing to release.
//   node scripts/release-pr.mjs --current       prints the current version
//   node scripts/release-pr.mjs --ci-inputs     prints "-f k=v ..." for dispatching CI
//   node scripts/release-pr.mjs --check vX.Y.Z [--notes notes.md]
//       Checks every version file and the changelog against the tag and
//       writes that version's section as release notes.
//
// The bump comes from the headings under [Unreleased]: "### Breaking" is a
// major, "### Added" or "### Removed" a minor, any other heading a patch.
// A repo's first release (never tagged, no versioned section yet) ships the
// version its files already declare.
// Dependabot PRs merged since the current version (prs.json: [{number, title}])
// that the section doesn't mention are added as one line under "### Changed".
//
// .github/release.json (all optional):
//   { "versionFiles": ["package.json", "server/package.json"],
//     "dependabotDirs": { "/server": "server" },
//     "ciDispatchInputs": { "e2e": false } }
// versionFiles may include .py files holding `__version__ = "X.Y.Z"` or a
// CONSTANT_VERSION; the first file is the source of the current version.
// versionFiles default: the first of package.json, pyproject.toml, Cargo.toml,
// VERSION that exists. A package.json's sibling package-lock.json is updated too.
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { execFileSync } from 'node:child_process'
import process from 'node:process'

// Output goes through process, so ESLint passes under browser or Node globals alike.
const say = (s) => process.stdout.write(`${s}\n`)

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/

export function parseVersion(v) {
  const m = SEMVER.exec(v)
  if (!m) throw new Error(`not a release version: ${v}`)
  return m.slice(1).map(Number)
}

export function bumpFor(headings) {
  const h = headings.map((x) => x.toLowerCase())
  if (h.includes('breaking')) return 'major'
  if (h.includes('added') || h.includes('removed')) return 'minor'
  return headings.length ? 'patch' : null
}

export function nextVersion(current, bump) {
  const [ma, mi, pa] = parseVersion(current)
  if (bump === 'major') return `${ma + 1}.0.0`
  if (bump === 'minor') return `${ma}.${mi + 1}.0`
  return `${ma}.${mi}.${pa + 1}`
}

// "chore(deps): Bump hono from 4.13.13 to 4.13.14 in /server" -> "hono 4.13.14 (server)"
export function dependabotItem(title, dirs = {}) {
  const t = title.replace(/^[a-z]+(\([^)]*\))?:\s*/i, '')
  const m = /^Bump (\S+) from \S+ to (\S+)(?: in (\S+))?/i.exec(t)
  if (!m) return { item: t.charAt(0).toLowerCase() + t.slice(1), key: null }
  const where = m[3] && m[3] !== '/' ? ` (${dirs[m[3]] ?? m[3].replace(/^\//, '')})` : ''
  return { item: `${m[1]} ${m[2]}${where}`, key: `${m[1]} ${m[2]}` }
}

function sectionBounds(changelog, heading) {
  const start = changelog.indexOf(heading)
  if (start < 0) return null
  const bodyStart = start + heading.length
  const next = changelog.indexOf('\n## [', bodyStart)
  const end = next < 0 ? changelog.length : next + 1
  return { start, bodyStart, end }
}

export function plan(changelog, current, date, repoUrl, prs = [], dirs = {}, prevTagged = true) {
  const b = sectionBounds(changelog, '## [Unreleased]')
  if (!b) throw new Error('CHANGELOG.md has no ## [Unreleased] section')
  let section = changelog.slice(b.bodyStart, b.end).trim()

  const deps = prs
    .map((p) => ({ ...dependabotItem(p.title, dirs), number: p.number }))
    .filter((d) => !(d.key && section.includes(d.key)) && !section.includes(`#${d.number}`))
  if (deps.length) {
    const line = `- Dependency updates (Dependabot): ${deps.map((d) => `${d.item} (#${d.number})`).join(', ')}.`
    section = /^### Changed$/m.test(section)
      ? section.replace(/^### Changed\n/m, `### Changed\n${line}\n`)
      : `${section ? `${section}\n\n` : ''}### Changed\n${line}`
  }

  const headings = [...section.matchAll(/^### (.+)$/gm)].map((m) => m[1].trim())
  const bump = bumpFor(headings)
  if (!bump) return null
  // First release of a repo that has never been tagged ships its declared version.
  const firstRelease = !prevTagged && !/^## \[\d+\.\d+\.\d+\]/m.test(changelog)
  const version = firstRelease ? current : nextVersion(current, bump)

  let out = `${changelog.slice(0, b.start)}## [Unreleased]\n\n## [${version}] - ${date}\n\n${section}\n\n${changelog.slice(b.end)}`
  if (!repoUrl) return { version, bump, headings, section, changelog: out.replace(/\n{3,}/g, '\n\n') } // no remote yet: no links
  const first = prevTagged && !firstRelease ? `${repoUrl}/compare/v${current}...v${version}` : `${repoUrl}/releases/tag/v${version}`
  const links = `[Unreleased]: ${repoUrl}/compare/v${version}...HEAD\n[${version}]: ${first}`
  out = /^\[Unreleased\]: .*$/m.test(out) ? out.replace(/^\[Unreleased\]: .*$/m, links) : `${out.trimEnd()}\n\n${links}\n`
  return { version, bump, headings, section, changelog: out.replace(/\n{3,}/g, '\n\n') }
}

export function notesFor(changelog, version) {
  const b = sectionBounds(changelog, `## [${version}]`)
  if (!b) return null
  const body = changelog.slice(changelog.indexOf('\n', b.start) + 1, b.end)
  return body.replace(/^\[[^\]]+\]: .*$/gm, '').trim()
}

// ---- version files ----------------------------------------------------------

// Python: `__version__ = "X.Y.Z"` or an UPPER_CASE name ending in VERSION, e.g. CLIENT_VERSION.
const PY_VERSION = /^(\s*(?:__version__|[A-Z][A-Z0-9_]*VERSION)\s*=\s*)(["'])(\d+\.\d+\.\d+)\2/gm

const TOML_SECTION = { 'pyproject.toml': /^\[(project|tool\.poetry)\]$/, 'Cargo.toml': /^\[package\]$/ }

export function readVersion(path, text) {
  const name = path.split('/').pop()
  if (name === 'package.json') return JSON.parse(text).version
  if (name === 'VERSION') return text.trim()
  if (name === 'galaxy.yml') return /^version:\s*['"]?([^'"\s]+)/m.exec(text)?.[1]
  if (TOML_SECTION[name]) return tomlVersion(text, TOML_SECTION[name]).value
  if (name.endsWith('.py')) return [...text.matchAll(PY_VERSION)][0]?.[3]
  throw new Error(`don't know how to read a version from ${path}`)
}

function tomlVersion(text, sectionRe) {
  const lines = text.split('\n')
  let inSection = false
  for (let i = 0; i < lines.length; i++) {
    if (/^\[.*\]$/.test(lines[i].trim())) inSection = sectionRe.test(lines[i].trim())
    else if (inSection) {
      const m = /^(version\s*=\s*)(["'])([^"']*)\2/.exec(lines[i])
      if (m) return { lines, index: i, prefix: m[1], quote: m[2], value: m[3] }
    }
  }
  return { lines, index: -1, value: undefined }
}

export function writeVersion(path, text, version) {
  const name = path.split('/').pop()
  if (name === 'package.json' || name === 'package-lock.json') {
    const j = JSON.parse(text)
    j.version = version
    if (j.packages?.['']) j.packages[''].version = version
    return `${JSON.stringify(j, null, 2)}\n`
  }
  if (name === 'VERSION') return `${version}\n`
  if (name === 'galaxy.yml') return text.replace(/^(version:\s*['"]?)[^'"\s]+/m, `$1${version}`)
  if (TOML_SECTION[name]) {
    const t = tomlVersion(text, TOML_SECTION[name])
    if (t.index < 0) throw new Error(`no version in ${path}`)
    t.lines[t.index] = `${t.prefix}${t.quote}${version}${t.quote}`
    return t.lines.join('\n')
  }
  if (name.endsWith('.py')) {
    const current = readVersion(path, text)
    if (!current) throw new Error(`no __version__ or *VERSION assignment in ${path}`)
    // Only assignments holding the current version move, so unrelated pins stay put.
    return text.replace(PY_VERSION, (m, pre, q, v) => (v === current ? `${pre}${q}${version}${q}` : m))
  }
  throw new Error(`don't know how to write a version to ${path}`)
}

function config(root) {
  const p = join(root, '.github/release.json')
  const c = existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : {}
  const files = c.versionFiles ?? [['package.json', 'pyproject.toml', 'Cargo.toml', 'VERSION'].find((f) => existsSync(join(root, f)))].filter(Boolean)
  if (!files.length) throw new Error('no version file: set versionFiles in .github/release.json')
  return { versionFiles: files, dependabotDirs: c.dependabotDirs ?? {}, ciDispatchInputs: c.ciDispatchInputs ?? {} }
}

function repoUrl(root) {
  if (process.env.GITHUB_REPOSITORY) return `${process.env.GITHUB_SERVER_URL ?? 'https://github.com'}/${process.env.GITHUB_REPOSITORY}`
  let url
  try {
    url = execFileSync('git', ['-C', root, 'remote', 'get-url', 'origin'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return '' // local-only repo
  }
  return url.replace(/^git@([^:]+):/, 'https://$1/').replace(/\.git$/, '')
}

function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith('--')) continue
    const next = argv[i + 1]
    out[argv[i].slice(2)] = next === undefined || next.startsWith('--') ? true : (i++, next)
  }
  return out
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  const root = process.cwd()
  const cfg = config(root)
  const current = readVersion(cfg.versionFiles[0], readFileSync(join(root, cfg.versionFiles[0]), 'utf8'))
  if (args.current) return say(current)
  if (args['ci-inputs']) {
    for (const k of Object.keys(cfg.ciDispatchInputs)) if (!/^[A-Za-z0-9_-]+$/.test(k)) throw new Error(`bad CI input name: ${k}`)
    return say(Object.entries(cfg.ciDispatchInputs).map(([k, v]) => `-f ${k}=${String(v).replace(/[^A-Za-z0-9_.-]/g, '')}`).join(' '))
  }
  const changelog = readFileSync(join(root, 'CHANGELOG.md'), 'utf8')

  if (args.check) {
    const version = String(args.check).replace(/^v/, '')
    parseVersion(version)
    for (const f of cfg.versionFiles) {
      const v = readVersion(f, readFileSync(join(root, f), 'utf8'))
      if (v !== version) throw new Error(`${f} is ${v}, tag is v${version}`)
    }
    const notes = notesFor(changelog, version)
    if (!notes) throw new Error(`CHANGELOG.md has no section for ${version}`)
    if (args.notes) writeFileSync(args.notes, `${notes}\n`)
    say(notes)
    return
  }

  const prs = args.dependabot ? JSON.parse(readFileSync(args.dependabot, 'utf8')) : []
  const date = args.date ?? new Date().toISOString().slice(0, 10)
  const prevTagged = execFileSync('git', ['-C', root, 'tag', '--list', `v${current}`], { encoding: 'utf8' }).trim() !== ''
  const result = plan(changelog, current, date, repoUrl(root), prs, cfg.dependabotDirs, prevTagged)
  if (!result) {
    say('none')
    return
  }
  if (!args['dry-run']) {
    writeFileSync(join(root, 'CHANGELOG.md'), result.changelog)
    for (const f of cfg.versionFiles) {
      writeFileSync(join(root, f), writeVersion(f, readFileSync(join(root, f), 'utf8'), result.version))
      const lock = join(dirname(f), 'package-lock.json')
      if (f.endsWith('package.json') && existsSync(join(root, lock))) {
        writeFileSync(join(root, lock), writeVersion(lock, readFileSync(join(root, lock), 'utf8'), result.version))
      }
    }
  }
  if (args.body) {
    const why = { major: 'a **Breaking** entry', minor: 'an **Added** or **Removed** entry', patch: 'fixes, changes or dependency updates only' }[result.bump]
    writeFileSync(
      args.body,
      `Release ${result.version}, a ${result.bump} release from ${current}: Unreleased has ${why}.\n\n` +
        'This PR is kept up to date by `.github/workflows/release-pr.yml` on every push to the default branch. Merge it when you want to release; ' +
        '`release-tag.yml` then tags the merge commit and publishes the GitHub Release. To change the notes, edit **Unreleased** on the default branch, not this branch.\n\n' +
        `## ${result.version}\n\n${result.section}\n`,
    )
  }
  say(result.version)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch (e) {
    process.stderr.write(`release-pr: ${e.message}\n`)
    process.exit(1)
  }
}
