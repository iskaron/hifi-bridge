# Official image-signing public keys

Every `*.pem` file in this directory is an **official** publisher key (ECDSA P-256, SubjectPublicKeyInfo PEM) that the flash tool trusts
for image manifests. The directory is intentionally **empty** today: the official release signing key does not exist yet; it is created
offline by the owner (docs/HARDWARE_GATE_CHECKLIST.md G4 K-1, docs/UPDATE_TRUST.md). Until then only keys the user adds explicitly as
"custom publisher" are trusted, and an official download is shown as **not trusted**.

Never put a private key here.
