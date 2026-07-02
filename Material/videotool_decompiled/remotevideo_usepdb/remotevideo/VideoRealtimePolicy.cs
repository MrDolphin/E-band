using System;

namespace remotevideo;

internal static class VideoRealtimePolicy
{
	public const int TargetFrameIntervalMs = 200;
	public const int MaxFrameAgeMs = 2000;
	public const int MaxChunksPerFrame = 34;

	public static int GetRepairChunkBudget(int newFrameChunkCount)
	{
		if (newFrameChunkCount <= 0)
		{
			return 0;
		}

		// Restrict repair budget to prevent repair congestion.
		// Allow up to 40% of new chunk count, capped at 8 chunks per round.
		return Math.Min(8, (int)Math.Ceiling(newFrameChunkCount * 0.4));
	}

	public static int GetRepairVariant(int baseVariant, uint schedulingFrameId)
	{
		return baseVariant + (int)(schedulingFrameId % 251u);
	}

	public static bool IsExpired(long firstSeenMs, long nowMs)
	{
		return nowMs - firstSeenMs >= MaxFrameAgeMs;
	}

	public static int MaxEncodedBytes(int chunkPayloadBytes)
	{
		return Math.Max(1, chunkPayloadBytes) * MaxChunksPerFrame;
	}
}
