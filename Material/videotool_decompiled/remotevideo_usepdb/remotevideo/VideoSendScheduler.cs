using System;
using System.Collections.Generic;
using System.Linq;

namespace remotevideo;

internal sealed record VideoSendWorkItem(
	uint FrameId,
	IReadOnlyList<WirelessVideoFrame> Chunks,
	long FirstSeenMs,
	bool IsRepair);

internal sealed class VideoSendScheduler
{
	private sealed class PendingRepairFrame
	{
		public PendingRepairFrame(
			uint frameId,
			IReadOnlyList<WirelessVideoFrame> chunks,
			long firstSeenMs)
		{
			FrameId = frameId;
			Chunks = chunks;
			FirstSeenMs = firstSeenMs;
			RepairEligibleMs = firstSeenMs + 1L;
		}

		public uint FrameId { get; }

		public IReadOnlyList<WirelessVideoFrame> Chunks { get; }

		public long FirstSeenMs { get; }

		public long RepairEligibleMs { get; }

		public int NextChunkIndex { get; set; }
	}

	private readonly Queue<PendingRepairFrame> m_repairQueue = new();
	private readonly Dictionary<uint, PendingRepairFrame> m_pendingFrames = new();

	public bool TryEnqueueNewFrame(
		byte[] payload,
		uint frameId,
		long nowMs,
		ushort payloadBytes,
		out VideoSendWorkItem firstPass)
	{
		IReadOnlyList<WirelessVideoFrame> chunks = WirelessVideoFrame.Fragment(
			payload,
			frameId,
			(uint)(nowMs & uint.MaxValue),
			payloadBytes);
		firstPass = new VideoSendWorkItem(frameId, chunks, nowMs, false);
		PendingRepairFrame pending = new PendingRepairFrame(frameId, chunks, nowMs);
		m_pendingFrames[frameId] = pending;
		m_repairQueue.Enqueue(pending);
		return true;
	}

	public IReadOnlyList<VideoSendWorkItem> DrainRepairWork(long nowMs, int newFrameChunkCount)
	{
		DropExpired(nowMs);
		int repairBudget = VideoRealtimePolicy.GetRepairChunkBudget(newFrameChunkCount);
		if (repairBudget <= 0 || m_repairQueue.Count == 0)
		{
			return Array.Empty<VideoSendWorkItem>();
		}

		List<VideoSendWorkItem> repairs = new();
		int repairedChunks = 0;
		int scannedFrames = 0;
		while (m_repairQueue.Count > 0 &&
			repairedChunks < repairBudget &&
			scannedFrames < m_repairQueue.Count)
		{
			PendingRepairFrame pending = m_repairQueue.Dequeue();
			scannedFrames++;
			if (!m_pendingFrames.TryGetValue(pending.FrameId, out PendingRepairFrame active) ||
				!ReferenceEquals(active, pending))
			{
				continue;
			}
			if (pending.RepairEligibleMs > nowMs)
			{
				m_repairQueue.Enqueue(pending);
				continue;
			}

			int remainingChunks = pending.Chunks.Count - pending.NextChunkIndex;
			if (remainingChunks <= 0)
			{
				m_pendingFrames.Remove(pending.FrameId);
				continue;
			}

			int chunkBudget = Math.Min(repairBudget - repairedChunks, remainingChunks);
			WirelessVideoFrame[] scheduledChunks = pending.Chunks
				.Skip(pending.NextChunkIndex)
				.Take(chunkBudget)
				.ToArray();
			if (scheduledChunks.Length == 0)
			{
				m_repairQueue.Enqueue(pending);
				continue;
			}

			repairs.Add(new VideoSendWorkItem(
				pending.FrameId,
				scheduledChunks,
				pending.FirstSeenMs,
				true));
			pending.NextChunkIndex += scheduledChunks.Length;
			repairedChunks += scheduledChunks.Length;
			if (pending.NextChunkIndex >= pending.Chunks.Count)
			{
				m_pendingFrames.Remove(pending.FrameId);
				continue;
			}

			m_repairQueue.Enqueue(pending);
		}

		return repairs;
	}

	public void DropExpired(long nowMs)
	{
		HashSet<uint> expiredFrameIds = m_pendingFrames
			.Where(pair => VideoRealtimePolicy.IsExpired(pair.Value.FirstSeenMs, nowMs))
			.Select(pair => pair.Key)
			.ToHashSet();
		if (expiredFrameIds.Count == 0)
		{
			return;
		}

		foreach (uint frameId in expiredFrameIds)
		{
			m_pendingFrames.Remove(frameId);
		}

		int queuedFrames = m_repairQueue.Count;
		for (int index = 0; index < queuedFrames; index++)
		{
			PendingRepairFrame pending = m_repairQueue.Dequeue();
			if (!expiredFrameIds.Contains(pending.FrameId))
			{
				m_repairQueue.Enqueue(pending);
			}
		}
	}
}
