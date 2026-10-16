"""Turn a typed passphrase into the encryption key (same code used when packing and on the live site)."""
import base64
import hashlib


def derive_key(passphrase: str) -> bytes:
    words = " ".join(passphrase.lower().split())          # case and extra spaces don't matter
    raw = hashlib.scrypt(words.encode(), salt=b"unbox-bundle-v1", n=2**16, r=8, p=1,
                         maxmem=2**28, dklen=32)
    return base64.urlsafe_b64encode(raw)
