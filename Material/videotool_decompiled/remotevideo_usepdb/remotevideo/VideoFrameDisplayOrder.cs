namespace remotevideo;

internal sealed class VideoFrameDisplayOrder
{
	private readonly object m_sync = new();
	private bool m_hasFrame;
	private uint m_latestFrameId;

	public bool TryAdvance(uint frameId)
	{
		lock (m_sync)
		{
			if (m_hasFrame && frameId <= m_latestFrameId)
			{
				return false;
			}

			m_hasFrame = true;
			m_latestFrameId = frameId;
			return true;
		}
	}

	public bool TryGetLatest(out uint frameId)
	{
		lock (m_sync)
		{
			frameId = m_latestFrameId;
			return m_hasFrame;
		}
	}

	public void Reset()
	{
		lock (m_sync)
		{
			m_hasFrame = false;
			m_latestFrameId = 0;
		}
	}
}
