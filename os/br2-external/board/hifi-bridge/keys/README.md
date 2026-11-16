# Update trust anchors (public certificates only)

* `release-ca.pem` - the **official** release CA certificate. **Does not exist yet**: it is created offline by the owner (docs/UPDATE_TRUST.md).
  Until then the choice "official releases" fails the build on purpose.
* Never put a private key here. `post-build.sh` refuses any input that contains one.
* Own CAs for self-built images live outside the repository (e.g. `.keys/`, ignored by git) and are referenced via
  `BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA`.
