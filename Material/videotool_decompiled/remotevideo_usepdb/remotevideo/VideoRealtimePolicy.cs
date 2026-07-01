using System;

namespace remotevideo;

internal static class VideoRealtimePolicy
{
	public const int TargetFrameIntervalMs = 200;
	public const int MaxFrameAgeMs = 500;
	public const int MaxChunksPerFrame = 34;

	public static int GetRepairChunkBudget(int newFrameChunkCount)
	{
		if (newFrameChunkCount <= 0)
		{
			return 0;
		}

		// repair / (new + repair) <= 20%
		return Math.Max(0, newFrameChunkCount / 4);
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
