# SignPath Setup

Chunes targets the free SignPath Foundation open-source code-signing program.
The public repository, Apache-2.0 license, privacy policy, code-signing policy,
GitHub-hosted release workflow, protected `main` branch, and existing Windows
release provide the application prerequisites.

## Apply

1. Apply at https://signpath.org/apply using this repository and its existing
   `v1.0.0` Windows release.
2. Install the SignPath GitHub App for `getchunes/chunes` when requested.
3. Create the SignPath project and upload `artifact-configuration.xml` as its
   default artifact configuration.
4. Create a release signing policy that uses the SignPath Foundation
   Authenticode certificate and requires manual approval.
5. Record the SignPath organization ID, project slug, signing policy slug, and
   submitter API token.

## GitHub Environments

The repository has two protected environments:

- `code-signing` requires approval before SignPath receives an artifact.
- `stable-release` requires a second approval before GitHub publishes it.

Set these secrets on the `code-signing` environment:

- `SIGNPATH_API_TOKEN`
- `SIGNPATH_ORGANIZATION_ID`
- `SIGNPATH_PROJECT_SLUG`
- `SIGNPATH_SIGNING_POLICY_SLUG`

Do not put secret values in this repository, workflow files, logs, issues, or
release notes.

## Artifact Policy

The artifact configuration accepts only `Chunes-${version}-x64.msi`. SignPath
must extract and Authenticode-sign the embedded `Chunes.exe`, enforce its Chunes
name/version/company metadata, rebuild the MSI, and Authenticode-sign the final
MSI. The workflow independently verifies Windows trust, publisher, MSI product
identity, UpgradeCode, and SHA-256 before publication.

## First Signed Release

Keep the existing zero-download `v1.0.0` release and tag in place while SignPath
reviews the project. Once approval, configuration, environment secrets, and the
reviewed `main` commit are all ready:

1. Confirm both current release assets still have zero downloads.
2. Delete the old `v1.0.0` release and tag.
3. Immediately dispatch `Sign and release` from `main` with version `1.0.0` and
   `confirm_v1_recreation` checked.
4. Approve the `code-signing` environment and the SignPath request.
5. After signature verification passes, approve `stable-release`.
6. Verify the published MSI from a clean Windows account before directing users
   to it.

If signing fails, do not publish the unsigned MSI and do not remove the existing
release early.
