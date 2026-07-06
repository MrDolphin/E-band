using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace remotevideo;

internal enum WirelessPayloadType : byte
{
	Video = 1,
	Control = 2,
	Acknowledgement = 3,
}

internal static class WirelessChunkSchedule
{
	public static int GetIndex(int chunkCount, int repeat, int position)
	{
		if (chunkCount <= 0)
		{
			throw new ArgumentOutOfRangeException(nameof(chunkCount));
		}
		if ((uint)position >= (uint)chunkCount)
		{
			throw new ArgumentOutOfRangeException(nameof(position));
		}

		int start = (repeat * 11) % chunkCount;
		int direction = (repeat & 1) == 0 ? 1 : -1;
		int index = (start + direction * position) % chunkCount;
		return index < 0 ? index + chunkCount : index;
	}
}

internal sealed record WirelessVideoFrame(
	WirelessPayloadType PayloadType,
	uint FrameId,
	ushort ChunkIndex,
	ushort ChunkCount,
	uint TimestampMs,
	byte[] Payload)
{
	public const uint SyncWord = 0x91D35555u;
	public const byte Version = 1;
	public const int HeaderSize = 20;
	public const int CrcSize = 4;
	public const int MaxPayloadSize = 1024;

	public byte[] Serialize()
	{
		if (Payload.Length > MaxPayloadSize)
		{
			throw new InvalidDataException($"Wireless payload exceeds {MaxPayloadSize} bytes.");
		}
		if (ChunkCount == 0 || ChunkIndex >= ChunkCount)
		{
			throw new InvalidDataException("Invalid wireless video chunk index/count.");
		}

		byte[] result = new byte[HeaderSize + Payload.Length + CrcSize];
		BinaryPrimitives.WriteUInt32LittleEndian(result.AsSpan(0, 4), SyncWord);
		result[4] = Version;
		result[5] = (byte)PayloadType;
		BinaryPrimitives.WriteUInt32LittleEndian(result.AsSpan(6, 4), FrameId);
		BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(10, 2), ChunkIndex);
		BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(12, 2), ChunkCount);
		BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(14, 2), (ushort)Payload.Length);
		BinaryPrimitives.WriteUInt32LittleEndian(result.AsSpan(16, 4), TimestampMs);
		Payload.CopyTo(result, HeaderSize);
		uint crc = Crc32.Compute(result.AsSpan(0, result.Length - CrcSize));
		BinaryPrimitives.WriteUInt32LittleEndian(result.AsSpan(result.Length - CrcSize), crc);
		return result;
	}

	public static bool TryParse(ReadOnlySpan<byte> data, out WirelessVideoFrame frame)
	{
		frame = null;
		if (data.Length < HeaderSize + CrcSize ||
			BinaryPrimitives.ReadUInt32LittleEndian(data) != SyncWord ||
			data[4] != Version)
		{
			return false;
		}

		ushort payloadLength = BinaryPrimitives.ReadUInt16LittleEndian(data.Slice(14, 2));
		int expectedLength = HeaderSize + payloadLength + CrcSize;
		if (payloadLength > MaxPayloadSize || data.Length != expectedLength)
		{
			if (!TryInferPayloadLength(data, out payloadLength))
			{
				return false;
			}
			expectedLength = data.Length;
		}

		uint expectedCrc = BinaryPrimitives.ReadUInt32LittleEndian(data.Slice(expectedLength - CrcSize));
		if (!HasValidCrc(data, expectedLength, payloadLength, expectedCrc))
		{
			return false;
		}

		ushort chunkIndex = BinaryPrimitives.ReadUInt16LittleEndian(data.Slice(10, 2));
		ushort chunkCount = BinaryPrimitives.ReadUInt16LittleEndian(data.Slice(12, 2));
		if (chunkCount == 0 || chunkIndex >= chunkCount)
		{
			return false;
		}

		frame = new WirelessVideoFrame(
			(WirelessPayloadType)data[5],
			BinaryPrimitives.ReadUInt32LittleEndian(data.Slice(6, 4)),
			chunkIndex,
			chunkCount,
			BinaryPrimitives.ReadUInt32LittleEndian(data.Slice(16, 4)),
			data.Slice(HeaderSize, payloadLength).ToArray());
		return true;
	}

	private static bool TryInferPayloadLength(
		ReadOnlySpan<byte> data,
		out ushort payloadLength)
	{
		payloadLength = 0;
		int inferredLength = data.Length - HeaderSize - CrcSize;
		if (inferredLength < 0 || inferredLength > MaxPayloadSize)
		{
			return false;
		}
		payloadLength = (ushort)inferredLength;
		return true;
	}

	private static bool HasValidCrc(
		ReadOnlySpan<byte> data,
		int expectedLength,
		ushort payloadLength,
		uint expectedCrc)
	{
		if (Crc32.Compute(data.Slice(0, expectedLength - CrcSize)) == expectedCrc)
		{
			return true;
		}

		ushort encodedPayloadLength =
			BinaryPrimitives.ReadUInt16LittleEndian(data.Slice(14, 2));
		if (encodedPayloadLength == payloadLength)
		{
			return false;
		}

		byte[] correctedHeader = data.Slice(0, expectedLength - CrcSize).ToArray();
		BinaryPrimitives.WriteUInt16LittleEndian(
			correctedHeader.AsSpan(14, 2),
			payloadLength);
		return Crc32.Compute(correctedHeader) == expectedCrc;
	}

	public static IReadOnlyList<WirelessVideoFrame> Fragment(
		ReadOnlySpan<byte> payload,
		uint frameId,
		uint timestampMs,
		int maxPayloadSize = MaxPayloadSize)
	{
		if (maxPayloadSize <= 0 || maxPayloadSize > MaxPayloadSize)
		{
			throw new ArgumentOutOfRangeException(nameof(maxPayloadSize));
		}

		int chunkCountValue = Math.Max(1, (payload.Length + maxPayloadSize - 1) / maxPayloadSize);
		if (chunkCountValue > ushort.MaxValue)
		{
			throw new InvalidDataException("Video frame requires too many wireless chunks.");
		}

		ushort chunkCount = (ushort)chunkCountValue;
		List<WirelessVideoFrame> frames = new List<WirelessVideoFrame>(chunkCount);
		for (ushort chunkIndex = 0; chunkIndex < chunkCount; chunkIndex++)
		{
			int offset = chunkIndex * maxPayloadSize;
			int length = Math.Min(maxPayloadSize, payload.Length - offset);
			frames.Add(new WirelessVideoFrame(
				WirelessPayloadType.Video,
				frameId,
				chunkIndex,
				chunkCount,
				timestampMs,
				payload.Slice(offset, length).ToArray()));
		}
		return frames;
	}
}

internal sealed class WirelessVideoReassembler
{
	private readonly object sync = new();
	private readonly Dictionary<uint, PendingFrame> pendingFrames = new();
	private readonly HashSet<uint> completedFrameIds = new();
	private string latestStatus = "尚未收到有效视频分片";

	public string LatestStatus
	{
		get
		{
			lock (sync)
			{
				return latestStatus;
			}
		}
	}

	public bool TryAdd(WirelessVideoFrame chunk, out byte[] completedPayload)
	{
		lock (sync)
		{
			return TryAddCore(chunk, out completedPayload);
		}
	}

	public bool TryGetMissingChunkIndices(
		uint frameId,
		out ushort[] missingChunkIndices,
		out bool completed)
	{
		lock (sync)
		{
			completed = completedFrameIds.Contains(frameId);
			if (completed)
			{
				missingChunkIndices = Array.Empty<ushort>();
				return true;
			}
			if (!pendingFrames.TryGetValue(frameId, out PendingFrame pending))
			{
				missingChunkIndices = Array.Empty<ushort>();
				return false;
			}

			missingChunkIndices = Enumerable.Range(0, pending.ChunkCount)
				.Select(index => (ushort)index)
				.Where(index => !pending.Chunks.ContainsKey(index))
				.ToArray();
			return true;
		}
	}

	public void Clear()
	{
		lock (sync)
		{
			pendingFrames.Clear();
			completedFrameIds.Clear();
			latestStatus = "尚未收到有效视频分片";
		}
	}

	private bool TryAddCore(WirelessVideoFrame chunk, out byte[] completedPayload)
	{
		completedPayload = null;
		if (chunk.PayloadType != WirelessPayloadType.Video)
		{
			return false;
		}
		if (completedFrameIds.Contains(chunk.FrameId))
		{
			latestStatus = $"帧 {chunk.FrameId}: 已完整恢复，忽略重复分片";
			return false;
		}

		if (!pendingFrames.TryGetValue(chunk.FrameId, out PendingFrame pending) ||
			pending.ChunkCount != chunk.ChunkCount)
		{
			pending = new PendingFrame(chunk.ChunkCount);
			pendingFrames[chunk.FrameId] = pending;
		}

		if (chunk.TimestampMs == 0xFEC0FFEEu)
		{
			pending.HasFecParity = true;
		}

		pending.Chunks[chunk.ChunkIndex] = chunk.Payload;

		// 2D XOR FEC (二维奇偶校验前向纠错) 恢复逻辑
		if (pending.HasFecParity && pending.ChunkCount > 3)
		{
			ushort originalCount = (ushort)(pending.ChunkCount - 3);

			// 检查有哪些原始分片依然缺失
			List<ushort> lostOriginals = new List<ushort>();
			for (ushort index = 0; index < originalCount; index++)
			{
				if (!pending.Chunks.ContainsKey(index))
				{
					lostOriginals.Add(index);
				}
			}

			if (lostOriginals.Count == 1)
			{
				// 情形一：只缺失 1 个原始分片，且收到了全量校验分片 (Index = originalCount)
				ushort lostIdx = lostOriginals[0];
				ushort parityAllIdx = originalCount;
				if (pending.Chunks.TryGetValue(parityAllIdx, out byte[] parityAll))
				{
					byte[] restored = (byte[])parityAll.Clone();
					bool canRestore = true;
					for (ushort index = 0; index < originalCount; index++)
					{
						if (index == lostIdx) continue;
						if (pending.Chunks.TryGetValue(index, out byte[] other))
						{
							if (other.Length != restored.Length) { canRestore = false; break; }
							for (int i = 0; i < restored.Length; i++) restored[i] ^= other[i];
						}
						else
						{
							canRestore = false;
							break;
						}
					}
					if (canRestore)
					{
						pending.Chunks[lostIdx] = restored;
						lostOriginals.Clear(); // 成功还原，缺失原始片归零
					}
				}
			}
			else if (lostOriginals.Count == 2)
			{
				// 情形二：缺失了 2 个原始分片，且正好一个是偶数、一个是奇数
				ushort lost0 = lostOriginals[0];
				ushort lost1 = lostOriginals[1];
				if ((lost0 % 2 == 0 && lost1 % 2 == 1) || (lost0 % 2 == 1 && lost1 % 2 == 0))
				{
					ushort lostEven = lost0 % 2 == 0 ? lost0 : lost1;
					ushort lostOdd = lost0 % 2 == 1 ? lost0 : lost1;
					ushort parityEvenIdx = (ushort)(originalCount + 1);
					ushort parityOddIdx = (ushort)(originalCount + 2);

					if (pending.Chunks.TryGetValue(parityEvenIdx, out byte[] parityEven) &&
						pending.Chunks.TryGetValue(parityOddIdx, out byte[] parityOdd))
					{
						// 尝试恢复偶数分片
						byte[] restoredEven = (byte[])parityEven.Clone();
						bool canRestoreEven = true;
						for (ushort index = 0; index < originalCount; index += 2)
						{
							if (index == lostEven) continue;
							if (pending.Chunks.TryGetValue(index, out byte[] other))
							{
								if (other.Length != restoredEven.Length) { canRestoreEven = false; break; }
								for (int i = 0; i < restoredEven.Length; i++) restoredEven[i] ^= other[i];
							}
							else
							{
								canRestoreEven = false;
								break;
							}
						}

						// 尝试恢复奇数分片
						byte[] restoredOdd = (byte[])parityOdd.Clone();
						bool canRestoreOdd = true;
						for (ushort index = 1; index < originalCount; index += 2)
						{
							if (index == lostOdd) continue;
							if (pending.Chunks.TryGetValue(index, out byte[] other))
							{
								if (other.Length != restoredOdd.Length) { canRestoreOdd = false; break; }
								for (int i = 0; i < restoredOdd.Length; i++) restoredOdd[i] ^= other[i];
							}
							else
							{
								canRestoreOdd = false;
								break;
							}
						}

						if (canRestoreEven && canRestoreOdd)
						{
							pending.Chunks[lostEven] = restoredEven;
							pending.Chunks[lostOdd] = restoredOdd;
							lostOriginals.Clear(); // 成功还原两个，缺失归零
						}
					}
				}
			}

			// 如果所有的原始分片已经凑齐（不管是收齐还是 FEC 恢复），就代表这帧接收重构完成！
			if (lostOriginals.Count == 0)
			{
				using MemoryStream stream = new MemoryStream();
				for (ushort index = 0; index < originalCount; index++)
				{
					stream.Write(pending.Chunks[index]);
				}
				completedPayload = stream.ToArray();
				pendingFrames.Remove(chunk.FrameId);
				completedFrameIds.Add(chunk.FrameId);
				latestStatus = $"帧 {chunk.FrameId}: 已通过 FEC/接收 完整恢复 {originalCount} 原始分片";
				DiscardOldFrames(chunk.FrameId);
				return true;
			}
		}
		else
		{
			// Fallback: 普通原始无 FEC 的重组逻辑
			if (pending.Chunks.Count == pending.ChunkCount)
			{
				using MemoryStream stream = new MemoryStream();
				for (ushort index = 0; index < pending.ChunkCount; index++)
				{
					stream.Write(pending.Chunks[index]);
				}
				completedPayload = stream.ToArray();
				pendingFrames.Remove(chunk.FrameId);
				completedFrameIds.Add(chunk.FrameId);
				latestStatus = $"帧 {chunk.FrameId}: 已完整恢复 {pending.ChunkCount}/{pending.ChunkCount}";
				DiscardOldFrames(chunk.FrameId);
				return true;
			}
		}

		latestStatus = BuildStatus(chunk.FrameId, pending);
		DiscardOldFrames(chunk.FrameId);
		return false;
	}

	public string GetStatus(uint frameId)
	{
		lock (sync)
		{
			if (completedFrameIds.Contains(frameId))
			{
				return $"帧 {frameId}: 已完整恢复";
			}
			if (!pendingFrames.TryGetValue(frameId, out PendingFrame pending))
			{
				return $"帧 {frameId}: 尚未收到有效分片";
			}
			return BuildStatus(frameId, pending);
		}
	}

	private static string BuildStatus(uint frameId, PendingFrame pending)
	{
		ushort[] missing = Enumerable.Range(0, pending.ChunkCount)
			.Select(index => (ushort)index)
			.Where(index => !pending.Chunks.ContainsKey(index))
			.ToArray();
		string missingText = missing.Length == 0
			? "无"
			: string.Join(",", missing
				.Take(16)
				.Select(index => index + 1)) +
				(missing.Length > 16 ? $",...共{missing.Length}个" : "");
		return $"帧 {frameId}: 已收 {pending.Chunks.Count}/{pending.ChunkCount}, 缺失 [{missingText}]";
	}

	private void DiscardOldFrames(uint newestFrameId)
	{
		foreach (uint frameId in pendingFrames.Keys
			.Where(id => newestFrameId > id && newestFrameId - id > 32)
			.ToArray())
		{
			pendingFrames.Remove(frameId);
		}
		foreach (uint frameId in completedFrameIds
			.Where(id => newestFrameId > id && newestFrameId - id > 32)
			.ToArray())
		{
			completedFrameIds.Remove(frameId);
		}
	}

	private sealed class PendingFrame
	{
		public ushort ChunkCount { get; }
		public Dictionary<ushort, byte[]> Chunks { get; } = new();
		public bool HasFecParity { get; set; }

		public PendingFrame(ushort chunkCount)
		{
			ChunkCount = chunkCount;
		}
	}
}
