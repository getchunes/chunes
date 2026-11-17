# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| Latest GitHub release | Yes |
| Latest Microsoft Store release | Yes |
| Earlier versions | No |

Only the newest release in each channel receives security fixes.

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

Official Windows releases use an MSI named `Chunes-<version>-x64.msi`. GitHub
releases are not Authenticode signed, so Windows reports **Unknown publisher**.
`.github/workflows/release-unsigned.yml` verifies that the raw EXE, embedded
EXE, and MSI are all `NotSigned`, checks the MSI identity, embeds a versioned
**Unknown publisher** warning, and publishes the release as `latest`. Microsoft
Store packages are signed by Microsoft during certification.

Repository release immutability must be enabled and confirmed by a maintainer
before the publication workflow is dispatched. The workflow creates an exact tag
at its reviewed commit, uploads the sole MSI to a draft, verifies GitHub's asset
digest, rechecks the tag, publishes, and then fails unless the release reports
immutable. Published asset bytes and tags are never replaced or reused; fixes
always receive a new version. SHA-256 verifies byte equality with the GitHub
asset but is not Authenticode publisher identity.

The in-app check looks for newer published numeric versions. It opens that
version's exact GitHub release page in the default browser and never downloads
or runs an installer. Updates are always installed manually.

Release builds compile only the crate versions pinned by `Cargo.lock`, built
with `--locked`, and use the hash-locked official WiX archive. GitHub Actions are full-SHA pinned. Only the protected publication job
receives `contents: write`, and it runs in its own protected environment.
