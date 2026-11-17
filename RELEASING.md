# Releasing Chunes

GitHub releases are published unsigned by `Publish unsigned manual release`
(`.github/workflows/release-unsigned.yml`). The Microsoft Store package is
built separately by `Build Microsoft Store MSIX` and signed by Partner Center.

The v1.0.0 interim release must remain unchanged forever. Do not delete,
recreate, retag, or replace any published MSI or tag.

Never store secret values in the repository, workflow files, build artifacts,
logs, issues, pull requests, release notes, or screenshots.

## GitHub release runbook

1. Choose the next unused version and set it as the `[package]` `version` in
   `Cargo.toml`, then run `cargo build` so `Cargo.lock` follows. `Cargo.toml`
   is the single build version source: `build.rs` derives the executable's
   four-part file and product versions from it, and the build scripts and
   workflows read it through `scripts/get-version.ps1`. Synchronize the
   fallback `ProductVersion` in `installer/Chunes.wxs` and the `Identity`
   `Version` in `installer/msix/AppxManifest.xml` (as `x.y.z.0`) with it;
   `tests/release_files.rs` fails `cargo test` when they disagree.
2. Preserve installer UpgradeCode
   `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}` exactly so the release upgrades
   prior versions.
3. Merge only through the protected pull-request path after Windows CI and
   CodeQL pass on the reviewed change.
4. Confirm the `unsigned-manual-release` environment is protected and release
   immutability remains enabled.
5. From the merged `main` commit, manually dispatch
   `Publish unsigned manual release`, enter the synchronized version, and
   explicitly confirm the unsigned publication.
6. Verify the workflow proves the raw EXE, embedded EXE, and MSI are
   `NotSigned`, that the MSI identity matches, and that the MSI contains the
   versioned unsigned warning.
7. Publish as an immutable normal GitHub release with `make_latest=true` and
   confirm `/releases/latest` points to the new version.
8. Download the published MSI, verify the release's immutable badge and asset
   digest, and test a clean per-user installation and an in-place upgrade,
   including the Windows **Unknown publisher** prompt and installer warning.
