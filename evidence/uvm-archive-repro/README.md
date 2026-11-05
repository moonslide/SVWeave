# Rebuilding the locked uvm-core archive from its git tag (2026-11-05)

Claim checked: `docs/install.md` (offline/blocked-archive path) says that `git archive` of the
locked uvm-core commit piped through `gzip -n` reproduces the locked archive sha256 exactly.

* Host: one cloud session (Linux x86_64), `git version 2.43.0`, `gzip 1.12`.
* Lock (`toolchain.lock.json` sha256 `ea20baf9bc2da3cb6b9dc41f0234c67f925ea37c5691b0bfbbb1ff30f86ef5e9`):
  `uvm_core` tag `2020.3.1`, commit `78c06547a2a0a29b3dc9dcafae62b75b2ff61544`,
  `artifact_sha256` `f55bdbc02cc500d4a2f41b31bad127653289eb2073f806ce156fb82662b362b8`.
* Commands (in an empty scratch directory):

```text
git clone -q --depth 1 --branch 2020.3.1 https://github.com/accellera-official/uvm-core src
git -C src rev-parse HEAD
git -C src archive --format=tar --prefix=uvm-core-2020.3.1/ 78c06547a2a0a29b3dc9dcafae62b75b2ff61544 | gzip -n > uvm.tar.gz
sha256sum uvm.tar.gz
```

* Output:

```text
78c06547a2a0a29b3dc9dcafae62b75b2ff61544
f55bdbc02cc500d4a2f41b31bad127653289eb2073f806ce156fb82662b362b8  uvm.tar.gz
```

Result: **PASS**, the sha256 equals the lock. Another git or gzip may produce other bytes; the
installer re-verifies the sha256 and refuses any other archive, so this record is about the
documented procedure, not about the safety of the install.
