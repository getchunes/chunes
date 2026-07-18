# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| Latest signed 1.0.x | Yes |
| Unsigned 1.0.0 | Until signed v1.0.1 is published |
| Earlier versions | No |

Only the latest stable Chunes release receives security fixes. Unsigned v1.0.0
becomes unsupported as soon as the signed v1.0.1 upgrade is published.

## Reporting a vulnerability

Please report suspected vulnerabilities through GitHub's private security
advisory form:

https://github.com/getchunes/chunes/security/advisories/new

Do not open a public issue for an unpatched vulnerability. Include the affected
version, impact, reproduction steps, and any proposed mitigation. Do not include
Discord credentials, browser data, private logs, or another person's data.

The maintainers will acknowledge a usable report, investigate it, and coordinate
disclosure and a fix when warranted. Response and release timing depends on
severity and maintainer availability; this policy does not promise a fixed SLA.

## Release trust

Official Windows releases use an MSI named `Chunes-<version>-x64.msi`. Chunes
v1.0.0 is the sole explicit exception to the normal signing guarantee: its
interim MSI is intentionally unsigned and Windows reports **Unknown publisher**.
It is published once by `.github/workflows/unsigned-v1.0.0.yml` while SignPath
Foundation approval is pending. That one-time workflow is a historical v1-only
exception, not a reusable unsigned release path.

Beginning with v1.0.1, every official MSI and its embedded `Chunes.exe` must be
Windows-trusted Authenticode signed with publisher exactly `SignPath Foundation`
before publication. An unsigned v1.0.1 or later release is prohibited.

Repository release immutability must be enabled and independently confirmed by
a maintainer before either publication workflow is dispatched. Each workflow
creates an exact tag at its reviewed commit, uploads the sole MSI to a draft,
verifies GitHub's asset digest, rechecks the tag, publishes, and then fails
unless the release reports immutable. Published asset bytes and tags are never
replaced or reused; fixes always receive a new version. SHA-256 verifies byte
equality with the GitHub asset but is not Authenticode publisher identity.

The in-app updater independently requires all of the following before it starts
Windows Installer:

- a newer stable GitHub release, not a draft or prerelease
- a release that reports `immutable: true`
- exactly one MSI with the expected versioned x64 filename
- the SHA-256 digest supplied by GitHub's release-asset API
- a Windows-trusted Authenticode signature from exactly `SignPath Foundation`
- exact MSI identity properties for Chunes, the selected release version,
  manufacturer `Chunes`, and UpgradeCode
  `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}`

An absent, invalid, untrusted, or differently published signature is a hard
failure. The unsigned v1.0.0 application does not accept unsigned updates and
has no bypass for its own unsigned origin. See the
[Code signing policy](README.md#code-signing-policy) for release roles and
controls.

The update helper runs from an encoded command through the Windows PowerShell
system binary rather than downloaded code. After Chunes exits, it repeats the
digest, Authenticode publisher, and MSI identity checks before invoking Windows
Installer. It waits for completion and relaunches installed Chunes on success;
on cancellation or failure it only relaunches the prior executable if its hash
still matches the running copy.

Release builds use hash-locked Python wheels and the hash-locked official WiX
archive. GitHub Actions are full-SHA pinned. Build, SignPath signing, and release
publication are separate jobs; only signing receives SignPath inputs, and only
the environment-protected publication job receives `contents: write`. The
v1.0.0 exception has no signing job or SignPath secrets and publishes only
through the dedicated protected `unsigned-v1-interim` environment.
