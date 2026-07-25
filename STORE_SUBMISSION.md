# Microsoft Store Submission

Chunes ships to the Microsoft Store as an MSIX package built by the
**Build Microsoft Store MSIX** workflow. The workflow stages a submission
artifact and stops there. It never tags, never creates a release, and never
touches the MSI channel.

## Why the package is unsigned

Partner Center signs the package during certification. A package signed before
upload is rejected. That also means the artifact **cannot be sideloaded** by
anyone who downloads it, which is why it is not attached to GitHub releases.
Direct downloads stay on the MSI channel.

## Package identity

These values must match the Product Identity page in Partner Center exactly.
They live in `installer/msix/AppxManifest.xml` and are asserted by
`tests/test_release_files.py`.

| Field | Value |
| --- | --- |
| Package/Identity/Name | `dubsector.dev.Chunes` |
| Package/Identity/Publisher | `CN=75E3A6B2-AC96-45EC-9B42-5EB66C83F2D2` |
| Package/Properties/PublisherDisplayName | `dubsector.dev` |

## Version numbers are permanent

The Store never lets a version number be reused, including after a failed
certification. Package version is always `<version.py>.0`, because the Store
reserves the fourth part.

A `store/v<version>` tag records every version that has been uploaded to
Partner Center. The workflow refuses to build a version that already carries
that tag. Push the tag right after a successful upload:

```powershell
git tag store/v1.0.12 <the commit the workflow built>
git push origin store/v1.0.12
```

Store versions and MSI release versions drift apart on purpose. Nothing
requires a Store submission to have a matching GitHub release, or the reverse.

## Cutting a Store build

1. Choose the next unused version and synchronize `version.py`, the fallback
   `ProductVersion` in `installer/Chunes.wxs`, and every version tuple and
   string in `installer/version_info.txt`.
2. Merge to `main` through the protected pull-request path after Windows CI and
   CodeQL pass. CI builds and checks the MSIX on every change, so packaging
   breakage surfaces before dispatch.
3. Dispatch **Build Microsoft Store MSIX** from `main` with that version.
4. Download the `store-msix-<version>` artifact and upload the `.msix` to the
   Partner Center submission.
5. Submit, then push the `store/v<version>` tag.

## If certification rejects the build

The version number is spent. Fix the finding, bump `version.py` to the next
version, and cut a fresh Store build. There is no need to publish a GitHub
release at the new version; the MSI channel can stay where it is.

## Testing the package locally

A Store package cannot be installed as built. Sign it with a throwaway
certificate first, trust that certificate once, then install:

```powershell
.\scripts\build-msix.ps1 -SelfSign
```

The script writes `dist\Chunes-devtest.cer` and prints the trust command, which
needs an elevated prompt:

```powershell
Import-Certificate -FilePath .\dist\Chunes-devtest.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople
Add-AppxPackage .\dist\Chunes-<version>-x64.msix
```

The development package uses the real Store identity, so uninstall it before
installing the Store build. Windows treats a publisher change on the same
identity as a conflict.

```powershell
Get-AppxPackage dubsector.dev.Chunes | Remove-AppxPackage
```

## What the packaged build does differently

`packaged.py` detects the package identity at runtime, so the Store build and
the MSI build are the same executable.

- Updates come from the Store. The tray menu hides **Automatically check for
  updates** and **Check for updates now**, and the GitHub update controller
  never starts.
- Autostart uses the manifest startup task rather than the Windows Run key. A
  packaged install path carries the package version, so a Run value would stop
  resolving after the next Store update. Windows Settings and Task Manager can
  override the app's own toggle, and `startup_task.py` opens
  **Settings > Apps > Startup** when Windows refuses the request.
- Settings, the log, and any config file follow the normal MSIX redirections
  for `HKCU` and `%LOCALAPPDATA%`. Nothing in the app writes to its install
  directory.

## Listing assets

Store listing artwork lives in the private `getchunes/brand-assets` repository
under `assets/store`. The package logos in `installer/msix/assets` are copied
from `assets/msix` in that repository and are regenerated with `npm run
generate`, never edited by hand.
