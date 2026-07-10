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
	private readonly Dictionary<uint, VideoSendWorkItem> m_pendingFrames = new();

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
		m_pendingFrames[frameId] = firstPass;
		return true;
	}

	public IReadOnlyList<VideoSendWorkItem> DrainRepairWork(long nowMs, int newFrameChunkCount)
	{
		DropExpired(nowMs);
		return Array.Empty<VideoSendWorkItem>();
	}

	public void DropExpired(long nowMs)
	{
		foreach (uint frameId in m_pendingFrames.Keys
			.Where(id => VideoRealtimePolicy.IsExpired(m_pendingFrames[id].FirstSeenMs, nowMs))
			.ToArray())
		{
			m_pendingFrames.Remove(frameId);
		}
	}
}
