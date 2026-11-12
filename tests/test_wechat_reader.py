import hashlib
import hmac
import sqlite3
import struct
import threading

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from xhs_importer.wechat_reader import decode_snapshot, rows_from_plain, discover_sources, read_stickers

KEY = bytes(range(32))
SALT = bytes(range(16))
PAGE = 4096


def database(tmp_path, md5='a' * 32):
    path = tmp_path / f'{md5}.db'
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE kNonStoreEmoticonTable (md5 TEXT, caption TEXT, product_id TEXT, aes_key TEXT, cdn_url TEXT, extern_url TEXT, encrypt_url TEXT, thumb_url TEXT)')
    con.execute('INSERT INTO kNonStoreEmoticonTable VALUES (?,?,?,?,?,?,?,?)',
                (md5, 'test', '', '', 'https://emoji.qpic.cn/original', '', '', ''))
    con.commit()
    con.close()
    data = bytearray(path.read_bytes())
    # Reserve 80 bytes for SQLCipher by relocating leaf cells in this small real DB.
    for offset in range(0, len(data), PAGE):
        base = offset + (100 if offset == 0 else 0)
        assert data[base] == 13
        assert data[base + 1:base + 3] == b'\0\0'
        count = int.from_bytes(data[base + 3:base + 5], 'big')
        start = int.from_bytes(data[base + 5:base + 7], 'big')
        cells = bytes(data[offset + start:offset + PAGE])
        data[offset + start - 80:offset + PAGE - 80] = cells
        data[offset + PAGE - 80:offset + PAGE] = bytes(80)
        data[base + 5:base + 7] = (start - 80).to_bytes(2, 'big')
        for i in range(count):
            pointer = base + 8 + i * 2
            value = int.from_bytes(data[pointer:pointer + 2], 'big') - 80
            data[pointer:pointer + 2] = value.to_bytes(2, 'big')
    data[20] = 80
    return bytes(data)


def encrypt_page(page, number):
    iv = number.to_bytes(16, 'little')
    start = 16 if number == 1 else 0
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(page[start:-80]) + encryptor.finalize()
    mac_key = hashlib.pbkdf2_hmac('sha512', KEY, bytes(v ^ 0x3a for v in SALT), 2, dklen=32)
    mac = hmac.new(mac_key, ciphertext + iv + number.to_bytes(4, 'little'), hashlib.sha512).digest()
    return (SALT if number == 1 else b'') + ciphertext + iv + mac


def encrypted(data):
    return b''.join(encrypt_page(data[i:i + PAGE], i // PAGE + 1) for i in range(0, len(data), PAGE))


def checksum(data, state=(0, 0), endian='<'):
    a, b = state
    words = struct.unpack(endian + 'I' * (len(data) // 4), data)
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) & 0xffffffff
        b = (b + words[i + 1] + a) & 0xffffffff
    return a, b


def wal(frames):
    header = struct.pack('>6I', 0x377f0682, 3007000, PAGE, 0, 100, 200)
    state = checksum(header)
    output = header + struct.pack('>2I', *state)
    for number, size, page in frames:
        prefix = struct.pack('>2I', number, size)
        payload = encrypt_page(page, number)
        state = checksum(prefix + payload, state)
        output += prefix + struct.pack('>4I', 100, 200, *state) + payload
    return output


def test_real_encrypted_database_and_committed_wal(tmp_path):
    old, new = database(tmp_path, 'a' * 32), database(tmp_path, 'b' * 32)
    plain = decode_snapshot(encrypted(old), b'', KEY, threading.Event())
    assert rows_from_plain(plain)[0]['md5'] == 'a' * 32
    log = wal([(2, 2, new[PAGE:]), (2, 0, old[PAGE:])])
    decoded = decode_snapshot(encrypted(old), log, KEY, threading.Event())
    assert rows_from_plain(decoded)[0]['md5'] == 'b' * 32
    # Stale frames after a WAL reset are not part of the current WAL generation.
    stale = bytearray(log)
    stale[32 + 24 + PAGE + 8:32 + 24 + PAGE + 16] = bytes(8)
    assert rows_from_plain(decode_snapshot(encrypted(old), bytes(stale), KEY, threading.Event()))[0]['md5'] == 'b' * 32


def test_mac_corruption_wrong_key_and_bad_wal_rejected(tmp_path):
    data = encrypted(database(tmp_path))
    broken = bytearray(data)
    broken[PAGE + 20] ^= 1
    with pytest.raises(ValueError, match='校验'):
        decode_snapshot(bytes(broken), b'', KEY, threading.Event())
    with pytest.raises(ValueError, match='校验'):
        decode_snapshot(data, b'', bytes(32), threading.Event())
    with pytest.raises(ValueError, match='WAL'):
        decode_snapshot(data, b'broken', KEY, threading.Event())


def test_cancelled_read_never_scans_memory(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    with pytest.raises(Exception, match='暂停'):
        read_stickers(tmp_path / 'emoticon.db', stop, lambda _: None)


def test_source_discovery_keeps_explicit_source_and_deduplicates(tmp_path):
    first = tmp_path / 'wxid_a/db_storage/emoticon/emoticon.db'
    second = tmp_path / 'wxid_b/db_storage/emoticon/emoticon.db'
    for path in (first, second):
        path.parent.mkdir(parents=True)
        path.touch()
    assert set(discover_sources(saved=first, roots=[tmp_path, tmp_path])) == {first.resolve(), second.resolve()}


def test_plain_reader_rejects_non_table_source_and_missing_columns(tmp_path):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE unrelated(value)')
    with pytest.raises(ValueError, match='表情'):
        rows_from_plain(con.serialize())
    con.close()
