from __future__ import annotations


def build_payload(sequence: int, size: int, pattern: str, message: bytes = b"") -> bytes:
    if size < 0:
        raise ValueError("payload size must be non-negative")
    if pattern == "message":
        return message
    if pattern == "counter":
        return bytes(((sequence + offset) & 0xFF) for offset in range(size))
    if pattern == "random":
        state = (0xA5A5A5A5 ^ sequence) & 0xFFFFFFFF
        payload = bytearray(size)
        for offset in range(size):
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            payload[offset] = (state >> 24) & 0xFF
        return bytes(payload)
    raise ValueError(f"unsupported payload pattern: {pattern}")


def raw_bitrate_bps(symbol_rate: float) -> float:
    return float(symbol_rate) * 2.0


def payload_efficiency(payload_size: int, encoded_packet_size: int, preamble_size: int) -> float:
    total_bytes_on_symbols = payload_size + (encoded_packet_size - payload_size) + preamble_size
    if total_bytes_on_symbols <= 0:
        return 0.0
    return float(payload_size) / float(total_bytes_on_symbols)
