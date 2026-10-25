# Release signing keys

Every release a screen installs over the air is signed with Ed25519, and the boot loader checks the
signature before anything is written ([docs/ota.md](../docs/ota.md)). The public keys a screen
trusts are frozen into the Quiver runtime from [`../boot/quiver_boot/keys.py`](../boot/quiver_boot/keys.py).

| Id | What it is | Trusted by |
| --- | --- | --- |
| `q1`, `q2` | Quiver's release keys: one in use, one reserve. The private keys are not in this repository and never on the API's servers. | Every Quiver runtime |
| `dev` | **`dev.key` in this folder: public on purpose.** For local development. | A screen trusts it only through `ota_keys` in board.json, and only while `api_url` is a local address. |

Only public keys are in `keys.py`. A new key needs a runtime release before screens trust it, since
the keys are frozen in; retiring one is the reverse.

## Your own key

For your own API and releases ([docs/ota.md](../docs/ota.md#diy)):

```
python tools/package.py keygen --out my.key          # keep my.key private; prints the public key
python tools/package.py app --version 3.1.0 --mpy
python tools/package.py sign dist/app-3.1.0 --key my.key --key-id me
python tools/package.py verify dist/app-3.1.0 --public-key <public key>
```

Trust it on your boards with `"ota_keys": {"me": "<public key>"}` in board.json, or in your own
runtime build with `python tools/package.py add-key me <public key>`.
