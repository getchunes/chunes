# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| Latest signed stable release | Yes |
| Latest explicitly labeled unsigned manual release | Yes |
| Earlier versions | No |

Only the newest release in the applicable signed-stable or unsigned-manual
channel receives security fixes. Release notes identify the trust and support
status; signing availability does not reserve a version or block a fix.

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

Official Windows releases use an MSI named `Chunes-<version>-x64.msi`. The
preferred stable channel requires both the MSI and embedded `Chunes.exe` to be
Windows-trusted Authenticode signed with publisher exactly `SignPath
Foundation`. Signed stable releases can be offered by the in-app updater.

If code signing is unavailable, `.github/workflows/release-unsigned.yml` may
publish a separately approved manual-only GitHub prerelease. It verifies that
the raw EXE, embedded EXE, and MSI are all `NotSigned`, embeds a versioned
**Unknown publisher** warning, and must not change `/releases/latest`. A later
signed build always uses a higher version; no release or tag is replaced.

Repository release immutability must be enabled and confirmed by a maintainer
before either publication workflow is dispatched. Each workflow
creates an exact tag at its reviewed commit, uploads the sole MSI to a draft,
verifies GitHub's asset digest, rechecks the tag, publishes, and then fails
unless the release reports immutable. Published asset bytes and tags are never
replaced or reused; fixes always receive a new version. SHA-256 verifies byte
equality with the GitHub asset but is not Authenticode publisher identity.

The active in-app check includes published stable and manual-prerelease numeric
versions. It opens a newer version's exact GitHub release page in the default
browser and does not download or run the MSI.

The signed automatic-install implementation is retained but currently inactive.
Before it can start Windows Installer, it independently requires all of the
following:

- a newer stable GitHub release, not a draft or prerelease
- a release that reports `immutable: true`
- exactly one MSI with the expected versioned x64 filename
- the SHA-256 digest supplied by GitHub's release-asset API
- a Windows-trusted Authenticode signature from exactly `SignPath Foundation`
- exact MSI identity properties for Chunes, the selected release version,
  manufacturer `Chunes`, and UpgradeCode
  `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}`

When that path is re-enabled, an absent, invalid, untrusted, or differently
published signature is a hard automatic-update failure. An unsigned application
does not accept unsigned updates and has no bypass for its own unsigned origin.
Unsigned releases require manual installation. See the
[Code signing policy](README.md#code-signing-policy) for release roles and
controls.

The update helper runs from an encoded command through the Windows PowerShell
system binary rather than downloaded code. After Chunes exits, it repeats the
digest, Authenticode publisher, and MSI identity checks before invoking Windows
Installer. It waits for completion and relaunches installed Chunes on success;
on cancellation or failure it only relaunches the prior executable if its hash
still matches the running copy.

Release builds use hash-locked Python wheels and the hash-locked official WiX
archive. GitHub Actions are full-SHA pinned. Build, signing, and publication are
separate jobs; only signing receives SignPath inputs, and only protected
publication jobs receive `contents: write`. The unsigned fallback has no signing
job or SignPath secrets and uses its own protected environment.
