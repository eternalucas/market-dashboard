"""Address format checks, so a mistyped address is caught before it's queried.

Bitcoin addresses carry a checksum (Base58Check for 1.../3..., bech32 or
bech32m for bc1...), so one wrong character fails the check. Ethereum
addresses are checked for shape (0x + 40 hex characters).
"""
from __future__ import annotations

import hashlib
import re

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    full = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + full


def _base58check_ok(addr: str) -> bool:
    try:
        raw = _b58decode(addr)
    except ValueError:
        return False
    if len(raw) != 25 or raw[0] not in (0x00, 0x05):  # P2PKH or P2SH, mainnet
        return False
    return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def _polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _bech32_ok(addr: str) -> bool:
    addr = addr.lower()
    hrp, _, data = addr.rpartition("1")
    if hrp != "bc" or len(data) < 6 or any(c not in BECH32 for c in data):
        return False
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + [BECH32.index(c) for c in data]
    const = _polymod(values)
    witness_version = BECH32.index(data[0])
    # v0 (bc1q...) uses bech32; v1+ (taproot, bc1p...) uses bech32m.
    return const == (1 if witness_version == 0 else 0x2BC830A3)


def valid_btc(addr: str) -> bool:
    if addr.lower().startswith("bc1"):
        return _bech32_ok(addr)
    return _base58check_ok(addr)


def valid_evm(addr: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", addr))


def valid(chain: str, addr: str) -> bool:
    if chain == "btc":
        return valid_btc(addr)
    if chain in ("eth", "bnb", "hyperliquid"):
        return valid_evm(addr)
    return True
