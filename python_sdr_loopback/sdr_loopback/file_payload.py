from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass


FILE_MAGIC = b"FIL1"
FILE_VERSION = 1
FILE_HEADER = struct.Struct("<4sBBIHHIIH")
FILE_HEADER_SIZE = FILE_HEADER.size


@dataclass(frozen=True)
class FileChunk:
    file_id: int
    chunk_index: int
    total_chunks: int
    file_size: int
    file_crc32: int
    chunk: bytes


def file_id_for(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def build_file_payloads(data: bytes, payload_size: int, file_id: int | None = None) -> list[bytes]:
    if payload_size <= FILE_HEADER_SIZE:
        raise ValueError(f"payload_size must be greater than {FILE_HEADER_SIZE}")
    file_id = file_id_for(data) if file_id is None else int(file_id) & 0xFFFFFFFF
    chunk_capacity = payload_size - FILE_HEADER_SIZE
    total_chunks = max(1, math.ceil(len(data) / chunk_capacity))
    if total_chunks > 0xFFFF:
        raise ValueError("file requires too many chunks for uint16 sequence numbers")

    crc = zlib.crc32(data) & 0xFFFFFFFF
    payloads: list[bytes] = []
    for chunk_index in range(total_chunks):
        start = chunk_index * chunk_capacity
        chunk = data[start : start + chunk_capacity]
        header = FILE_HEADER.pack(
            FILE_MAGIC,
            FILE_VERSION,
            FILE_HEADER_SIZE,
            file_id,
            chunk_index,
            total_chunks,
            len(data),
            crc,
            len(chunk),
        )
        payloads.append(header + chunk + bytes(payload_size - FILE_HEADER_SIZE - len(chunk)))
    return payloads


def parse_file_payload(payload: bytes) -> FileChunk:
    if len(payload) < FILE_HEADER_SIZE:
        raise ValueError("file payload is too short")
    magic, version, header_len, file_id, chunk_index, total_chunks, file_size, file_crc32, chunk_len = FILE_HEADER.unpack_from(
        payload
    )
    if magic != FILE_MAGIC:
        raise ValueError("bad file payload magic")
    if version != FILE_VERSION:
        raise ValueError(f"unsupported file payload version {version}")
    if header_len != FILE_HEADER_SIZE:
        raise ValueError("unsupported file payload header size")
    if total_chunks <= 0:
        raise ValueError("invalid total chunk count")
    if chunk_index >= total_chunks:
        raise ValueError("chunk index out of range")
    if chunk_len > len(payload) - FILE_HEADER_SIZE:
        raise ValueError("chunk length exceeds payload")
    return FileChunk(
        file_id=file_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        file_size=file_size,
        file_crc32=file_crc32,
        chunk=payload[FILE_HEADER_SIZE : FILE_HEADER_SIZE + chunk_len],
    )


def recover_file(payloads: list[bytes]) -> tuple[bytes, dict[str, int | list[int]]]:
    chunks = [parse_file_payload(payload) for payload in payloads]
    if not chunks:
        raise ValueError("no file payloads to recover")

    first = chunks[0]
    by_index: dict[int, bytes] = {}
    for chunk in chunks:
        if chunk.file_id != first.file_id:
            raise ValueError("mixed file ids")
        if chunk.total_chunks != first.total_chunks:
            raise ValueError("mixed total chunk counts")
        if chunk.file_size != first.file_size:
            raise ValueError("mixed file sizes")
        if chunk.file_crc32 != first.file_crc32:
            raise ValueError("mixed file CRC values")
        by_index.setdefault(chunk.chunk_index, chunk.chunk)

    missing = [index for index in range(first.total_chunks) if index not in by_index]
    if missing:
        return b"", {
            "file_id": first.file_id,
            "file_size": first.file_size,
            "file_crc32": first.file_crc32,
            "total_chunks": first.total_chunks,
            "chunks_ok": len(by_index),
            "missing_chunks": missing,
        }

    data = b"".join(by_index[index] for index in range(first.total_chunks))[: first.file_size]
    actual_crc = zlib.crc32(data) & 0xFFFFFFFF
    return data, {
        "file_id": first.file_id,
        "file_size": first.file_size,
        "file_crc32": first.file_crc32,
        "file_crc32_actual": actual_crc,
        "total_chunks": first.total_chunks,
        "chunks_ok": len(by_index),
        "missing_chunks": [],
    }
