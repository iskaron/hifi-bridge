# macOS build, Developer ID signing and notarisation of the flash tool

Status: **prepared, never executed.** No macOS runner has run the workflow, no certificate or API key exists, nothing was signed,
notarised or published (DECISIONS #126; hardware/OS gate G3 F-6). Windows code signing stays open (OPEN_QUESTIONS #69).

Owner decision (2026-11-20): macOS builds, Developer ID signing and notarisation run on **GitHub Actions** with the owner's existing Apple
Developer Program membership, not on Xcode Cloud. The **RAUC release key for appliance updates is unrelated** and stays offline
(docs/UPDATE_TRUST.md); nothing in this pipeline can sign an appliance update.

## 1. What exists
| File | Purpose |
|---|---|
| `.github/workflows/flashtool-macos.yml` | Manual workflow (`workflow_dispatch` only). Job `build`: unsigned `.app` for arm64 (`macos-15`) and x86_64 (`macos-15-intel`) plus smoke tests. Job `sign` (only with the input `sign = true`, in the environment `macos-signing`): sign, notarise, staple, `.dmg` |
| `flashtool/packaging/macos/build.sh` | PyInstaller one-folder `.app` (Qt stays a separate framework, LGPL), licence texts in `Contents/Resources/licenses`, `LSMinimumSystemVersion` vs. the bundled Qt's minimum, offscreen GUI self-test, the frozen helper writes a **simulated** card (a regular file) |
| `flashtool/packaging/macos/sign_and_notarize.sh` | Inside-out signing (every Mach-O file, then the frameworks, then the helper, the GUI, the bundle), hardened runtime, secure timestamp; `notarytool` with an App Store Connect API key (CI) or a keychain profile (owner's Mac); fails unless Apple answers `Accepted`; staples the app and the `.dmg` |
| `flashtool/packaging/macos/entitlements.plist` | Hardened-runtime entitlements - deliberately empty (section 4) |
| `flashtool/packaging/macos/requirements.txt` | The exact build pins (equal to `docker/Dockerfile.flashtool`, checked by `tests/entry/test_workflows.py`) |

`tests/entry/test_workflows.py` (STATIC, part of `scripts/run_entry_tests.sh`) checks: manual trigger only, signing opt-in and only in the
`macos-signing` environment, secrets only as step environment of the `sign` job (never expanded inside a command line), the keychain and the
API key are removed in an `always()` step, all actions SHA-pinned, no `--deep` signing, a rejected notarisation fails, empty entitlements.

## 2. What the owner needs from Apple
| Item | Needed? | Notes |
|---|---|---|
| **Developer ID Application** certificate | **yes** | Signs the `.app`, both executables, every bundled library and the `.dmg`. Created in the Apple Developer portal (Certificates → "+" → Developer ID → Developer ID Application, profile type "G2 Sub-CA"); Apple lets only the team's Account Holder create Developer ID certificates. |
| **Developer ID Installer** certificate | **no** (for the current design) | Only for a `.pkg` installer. The flash tool ships as a `.dmg` with a self-contained `.app`; the privileged helper lives inside the bundle and is elevated per write (section 5) - no launchd daemon, no `SMAppService`, nothing installed outside the `.app`. Needed only if that design changes. |
| **App Store Connect API key** (Team key) | **yes** (for notarisation in CI) | App Store Connect → Users and Access → Integrations → Team Keys → "+", access **Developer**. The `.p8` file can be downloaded **once**; note the Key ID and the Issuer ID. Preferred over an Apple ID + app-specific password: revocable on its own, no personal account in CI. |
| Team ID | yes | Part of the identity string `Developer ID Application: <Name> (<TEAMID>)`. |
| Provisioning profile | no | Not needed for Developer ID apps without restricted entitlements. |

## 3. One-time setup (owner; agents never create certificates or secrets)
1. **Certificate:** on a Mac, Keychain Access → Certificate Assistant → "Request a Certificate From a Certificate Authority" (saved to
   disk), upload the CSR in the portal, download and double-click the certificate. In Keychain Access export the certificate **with its
   private key** as `.p12` and a long random password. Keep an offline backup of the `.p12`; the private key cannot be downloaded again.
2. **API key:** create the Team key (section 2), download `AuthKey_<KEYID>.p8`.
3. **GitHub environment:** repository Settings → Environments → New environment `macos-signing`:
   * *Required reviewers*: the owner (every signing run then waits for an explicit approval);
   * *Deployment branches and tags*: selected branches only (`main`, optionally `develop` for test runs);
   * environment **secrets** (not repository secrets, so no other workflow or job can read them):

   | Secret | Content |
   |---|---|
   | `MACOS_DEVELOPER_ID_P12_BASE64` | `base64 -i DeveloperID.p12` (one line) |
   | `MACOS_DEVELOPER_ID_P12_PASSWORD` | the `.p12` export password |
   | `MACOS_SIGN_IDENTITY` | `Developer ID Application: <Name> (<TEAMID>)` exactly as `security find-identity -v -p codesigning` prints it |
   | `MACOS_NOTARY_KEY_P8_BASE64` | `base64 -i AuthKey_<KEYID>.p8` |
   | `MACOS_NOTARY_KEY_ID` | the API key's Key ID |
   | `MACOS_NOTARY_ISSUER_ID` | the Issuer ID shown above the Team keys |

   The temporary keychain password is generated per run; it is not a secret to store.
4. **First run:** Actions → `flashtool-macos` → "Run workflow" with `sign` **off** - checks the build and the smoke tests on both
   architectures without touching a secret. Then once with `sign` **on** and approve the environment. Download the `.dmg` artifacts.
5. **Check on a Mac** (not possible in this repository): `spctl --assess --type open --context context:primary-signature -v <dmg>`,
   `xcrun stapler validate <dmg>`, open the `.app` from `/Applications` on a Mac that has never seen it (Gatekeeper must not warn), then the
   flash-tool hardware tests G3 F-1…F-6 (elevation prompt, real card).

Cost note while the repository is private: macOS runner minutes are billed at a higher rate than Linux minutes (OPEN_QUESTIONS #44); the
workflow never starts by itself. Public repositories use standard GitHub-hosted runners for free.

## 4. Hardened runtime and entitlements
Notarisation requires the hardened runtime on every executable. `entitlements.plist` is empty on purpose:
* no App Sandbox: a sandboxed app can neither start the elevated helper nor open `/dev/rdiskN`;
* no `com.apple.security.cs.disable-library-validation`: every bundled library (Python, Qt, PySide6, cryptography) is re-signed with the
  same Team ID, so library validation passes;
* no `allow-unsigned-executable-memory` / `allow-jit`: no JIT; on macOS the platform adapter runs `diskutil` and uses `fcntl` only, no
  ctypes callbacks.

If a signed build fails at run time without an entitlement, add exactly that one, with the reason as a comment next to it, and extend
`tests/entry/test_workflows.py::test_the_entitlements_are_empty_on_purpose` accordingly.

## 5. The privileged helper
* `hififlash-helper` is a separate executable inside the bundle (`Contents/MacOS/`), built **without Qt**, signed with the same Developer ID
  and hardened runtime before the GUI and the bundle, so the bundle's seal covers it.
* Elevation stays as designed (docs/FLASH_TOOL.md §4): `osascript … do shell script "<helper> --connect …" with administrator
  privileges`, one write per prompt; the helper authenticates to the GUI with a one-time token over loopback and re-checks the target disk
  itself. No `SMJobBless`/`SMAppService` daemon, no setuid, nothing stays installed or elevated after the write.
* Location: users should drag the app to `/Applications` (admin-owned). A quarantined app started from `~/Downloads` runs translocated from a
  read-only path, which also prevents swapping the helper. Open point (OPEN_QUESTIONS #88): should the GUI check the helper's code signature
  (same Team ID, `codesign --verify -R`) before elevating it?

## 6. Not verified / risks
* Nothing here has run on macOS. Runner labels (`macos-15`, `macos-15-intel`) were taken from the runner-images list of 2026-11; Intel
  runners are being phased out by GitHub - drop the x86_64 entry (or build universal2) when they go.
* The bundled PySide6/Qt minimum macOS version is checked by `build.sh` against `LSMinimumSystemVersion` (12.0 in the spec); the first
  run may require raising it.
* PyInstaller layouts change between versions; if notarisation reports an unsigned binary, the notary log names it (printed by the script).
* No universal2 build (two separate artifacts instead).
