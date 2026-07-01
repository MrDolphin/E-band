namespace remotevideo;

internal static class VideoRxContinuityPolicy
{
	private const int MaxMissingFramesToPreserve = 2;

	public static bool ShouldResetDecoder(int previousFrameId, int currentFrameId)
	{
		if (previousFrameId < 0)
		{
			return false;
		}

		int missingFrames = currentFrameId - previousFrameId - 1;
		return missingFrames < 0 || missingFrames > MaxMissingFramesToPreserve;
	}
}
