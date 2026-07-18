# SignPath Setup and v1.0.1 Conversion

Chunes targets the free SignPath Foundation open-source code-signing program.
The intentionally unsigned v1.0.0 interim release must remain unchanged forever.
Do not delete, recreate, retag, or replace its MSI after publication. The first
signed release is v1.0.1.

## SignPath Setup

1. Complete the SignPath Foundation application for `getchunes/chunes`.
2. Install the SignPath GitHub App for the repository when approval permits it.
3. Create the SignPath project, upload `artifact-configuration.xml`, and select
   it as the default artifact configuration.
4. Create a release signing policy that uses the SignPath Foundation
   Authenticode certificate and requires manual approval.
5. Protect the `code-signing` environment and set these four environment
   secrets: `SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID`,
   `SIGNPATH_PROJECT_SLUG`, and `SIGNPATH_SIGNING_POLICY_SLUG`.
6. Keep `stable-release` separately protected for final publication approval.

Never store secret values in the repository, workflow files, build artifacts,
logs, issues, pull requests, release notes, or screenshots.

## Artifact Policy

The default artifact configuration accepts only
`Chunes-${version}-x64.msi`. SignPath extracts and Authenticode-signs the
embedded `Chunes.exe`, enforces its Chunes product, version, company, copyright,
and original-filename metadata, rebuilds the MSI, and then Authenticode-signs
the outer MSI. The workflow independently verifies both signatures, the exact
`SignPath Foundation` publisher, EXE metadata, MSI product identity, UpgradeCode,
and SHA-256 after signing and again before publication.

## Post-Approval v1.0.1 Runbook

1. Preserve the immutable v1.0.0 release, tag, and MSI forever. Do not use a
   recreation or replacement process.
2. Confirm the SignPath GitHub App, default `artifact-configuration.xml`, manual
   release signing policy, protected environments, all four `code-signing`
   secrets, and repository release immutability are configured.
3. Create a pull request that changes `version.py`, the fallback
   `ProductVersion` in `installer/Chunes.wxs`, and every version tuple/string in
   `installer/version_info.txt` from 1.0.0 to 1.0.1.
4. In the same pull request, remove the v1.0.0-only unsigned warning dialog and
   routing from `installer/Chunes.wxs`, delete
   `.github/workflows/unsigned-v1.0.0.yml`, and update documentation and tests to
   describe the completed signed transition.
5. Preserve installer UpgradeCode
   `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}` exactly so v1.0.1 upgrades v1.0.0.
6. Merge only through the protected pull-request path after Windows CI and
   CodeQL pass on the reviewed conversion.
7. From the merged `main` commit, manually dispatch `Sign and release` with
   version `1.0.1`. Never publish an unsigned v1.0.1 artifact.
8. Approve the protected `code-signing` job, then manually approve the SignPath
   signing request. Review the workflow's outer-MSI and embedded-EXE trust,
   publisher, identity, metadata, and digest results.
9. Approve `stable-release` only after every signing verification succeeds. The
   workflow must create the exact tag at `GITHUB_SHA`, upload the sole MSI to a
   draft, verify its GitHub digest, and publish it as an immutable latest
   release.
10. Download the published v1.0.1 MSI and verify the release's immutable badge
    and asset digest, the outer MSI signature, the extracted and installed
    `Chunes.exe` signature and metadata, a clean per-user installation, and an
    in-place upgrade from the immutable v1.0.0 MSI.

Both the outer MSI and embedded or installed EXE must be Windows-trusted and
identify exactly `SignPath Foundation`. If any check fails, do not publish or
distribute v1.0.1; fix the source or SignPath configuration through a new pull
request and rerun the signed workflow without weakening its checks.
