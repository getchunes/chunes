# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| Earlier versions | No |

Only the latest stable Chunes release receives security fixes.

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
release workflow publishes the MSI only after SignPath completes signing. The
Windows signature must be trusted and name `SignPath Foundation` as publisher.

The in-app updater independently requires all of the following before it starts
Windows Installer:

- a newer stable GitHub release, not a draft or prerelease
- exactly one MSI with the expected versioned x64 filename
- the SHA-256 digest supplied by GitHub's release-asset API
- a Windows-trusted Authenticode signature from exactly `SignPath Foundation`
- exact MSI identity properties for Chunes, the selected release version,
  manufacturer `Chunes`, and UpgradeCode
  `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}`

An absent, invalid, untrusted, or differently published signature is a hard
failure. See the [Code signing policy](README.md#code-signing-policy) for release
roles and controls.

The update helper runs from an encoded command through the Windows PowerShell
system binary rather than downloaded code. After Chunes exits, it repeats the
digest, Authenticode publisher, and MSI identity checks before invoking Windows
Installer. It waits for completion and relaunches installed Chunes on success;
on cancellation or failure it only relaunches the prior executable if its hash
still matches the running copy.

Release builds use hash-locked Python wheels and the hash-locked official WiX
archive. GitHub Actions are full-SHA pinned. Build, SignPath signing, and release
publication are separate jobs; only signing receives SignPath inputs, and only
the environment-protected publication job receives `contents: write`.
