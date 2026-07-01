namespace remotevideo;

internal sealed class VideoFrameDisplayOrder
{
	private bool m_hasFrame;
	private uint m_latestFrameId;

	public bool TryAdvance(uint frameId)
	{
		if (m_hasFrame && frameId <= m_latestFrameId)
		{
			return false;
		}

		m_hasFrame = true;
		m_latestFrameId = frameId;
		return true;
	}

	public void Reset()
	{
		m_hasFrame = false;
		m_latestFrameId = 0;
	}
}
