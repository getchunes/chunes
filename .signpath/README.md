# SignPath Setup and Release Fallback

Chunes targets the free SignPath Foundation open-source code-signing program.
The intentionally unsigned v1.0.0 interim release must remain unchanged forever.
Do not delete, recreate, retag, or replace its MSI after publication. No future
version is reserved for signing.

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

## Signed Release Runbook

1. Preserve the immutable v1.0.0 release, tag, and MSI forever. Do not use a
   recreation or replacement process.
2. Confirm the SignPath GitHub App, default `artifact-configuration.xml`, manual
   release signing policy, protected environments, all four `code-signing`
   secrets, and repository release immutability are configured.
3. Choose the next unused version and synchronize `version.py`, the fallback
   `ProductVersion` in `installer/Chunes.wxs`, and every version tuple/string in
   `installer/version_info.txt`.
4. Confirm the normal build omits the conditional unsigned manual-release
   warning and that documentation describes the selected release channel.
5. Preserve installer UpgradeCode
   `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}` exactly so the release upgrades prior versions.
6. Merge only through the protected pull-request path after Windows CI and
   CodeQL pass on the reviewed conversion.
7. From the merged `main` commit, manually dispatch `Sign and release` with the
   synchronized version.
8. Approve the protected `code-signing` job, then manually approve the SignPath
   signing request. Review the workflow's outer-MSI and embedded-EXE trust,
   publisher, identity, metadata, and digest results.
9. Approve `stable-release` only after every signing verification succeeds. The
   workflow must create the exact tag at `GITHUB_SHA`, upload the sole MSI to a
   draft, verify its GitHub digest, and publish it as an immutable latest
   release.
10. Download the published MSI and verify the release's immutable badge
    and asset digest, the outer MSI signature, the extracted and installed
    `Chunes.exe` signature and metadata, a clean per-user installation, and an
    in-place upgrade from the immutable v1.0.0 MSI.

Both the outer MSI and embedded or installed EXE must be Windows-trusted and
identify exactly `SignPath Foundation`. If verification fails, fix the source
or signing configuration through a new pull request and rerun the signed
workflow without weakening its checks.

## Unsigned Fallback Runbook

Use this path only when SignPath approval or service is unavailable, not when a
candidate fails security or identity verification.

1. Keep the same reviewed source and next unused version; never reuse a tag or
   replace a release asset.
2. Confirm the `unsigned-manual-release` environment is protected and release
   immutability remains enabled.
3. Manually dispatch `Publish unsigned manual release`, enter the synchronized
   version, and explicitly confirm the unsigned publication.
4. Verify the workflow proves the raw EXE, embedded EXE, and MSI are `NotSigned`
   and that the MSI contains the versioned unsigned warning.
5. Publish as an immutable normal GitHub release with `make_latest=true` and
   confirm `/releases/latest` points to the new version.
6. Test a manual install and in-place upgrade, including the Windows **Unknown
   publisher** prompt and installer warning.
7. If signing becomes available later, use the next higher version through the
   signed runbook. Never sign or replace the already published unsigned MSI.
