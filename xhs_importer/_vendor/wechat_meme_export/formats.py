"""Format and AES helpers excerpted from MIT upstream; no network or file I/O."""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def detect_format(data: bytes) -> str | None:
    if data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return "png"
    if data.startswith(bytes.fromhex("ffd8ff")):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def strip_pkcs7(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def aes_decrypt_variants(data: bytes, aes_key: str) -> tuple[bytes, str] | None:
    keys: list[bytes] = []
    try:
        key_hex = bytes.fromhex(aes_key)
        if len(key_hex) in (16, 24, 32):
            keys.append(key_hex)
    except ValueError:
        pass

    key_ascii = aes_key.encode("utf-8")
    if len(key_ascii) in (16, 24, 32):
        keys.append(key_ascii)

    usable = data[: len(data) - (len(data) % 16)]
    if not usable:
        return None

    for key in keys:
        for mode_name, mode in (("cbc0", modes.CBC(b"\x00" * 16)), ("ecb", modes.ECB())):
            try:
                decrypted = Cipher(algorithms.AES(key), mode).decryptor().update(usable)
            except ValueError:
                continue
            for candidate in (decrypted, strip_pkcs7(decrypted)):
                if detect_format(candidate):
                    return candidate, f"aes:{mode_name}"
    return None



