"""CPython stand-in for MicroPython's cryptolib, on the cryptography package (AES modes 1 = ECB, 2 = CBC)."""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class aes:
    def __init__(self, key, mode, iv=None):
        if mode != 2:
            raise ValueError("only CBC is simulated")
        self._c = Cipher(algorithms.AES(bytes(key)), modes.CBC(bytes(iv)))

    def decrypt(self, data):
        d = self._c.decryptor()
        return d.update(bytes(data)) + d.finalize()

    def encrypt(self, data):
        e = self._c.encryptor()
        return e.update(bytes(data)) + e.finalize()
