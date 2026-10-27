"""The keys release manifests may be signed with: id → raw Ed25519 public key, base64.

Frozen into the Quiver runtime, so a new key needs a runtime release (keys/README.md).
`tools/package.py add-key` adds entries. The development key is not here: a board trusts it only
through ota_keys in board.json, and only for a local api_url.
"""

KEYS = {
    "q1": "dlVu/IW0JT5/iaprxWGUYXK0GxjTA72oOzaNyqo24ls=",
    "q2": "1+RbZ4+mV/NqulTjBFCFfAcf/OIIJgqeIsrutOK5ZSM=",
}

# The well-known development key (keys/dev.key, public on purpose). Refused unless the API
# is on this network, so a board can never be talked into it by a server on the internet.
DEV = "OuFmKK2huk4vBe96SLOJWRr4jcqcVBrhH5zNhvADUQU="
