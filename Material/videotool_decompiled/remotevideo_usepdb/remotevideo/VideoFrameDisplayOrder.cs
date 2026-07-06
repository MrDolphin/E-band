namespace remotevideo;

internal sealed class VideoFrameDisplayOrder
{
	private readonly object m_lock = new();
	private bool m_hasFrame;
	private uint m_latestFrameId;

	public bool TryAdvance(uint frameId)
	{
		lock (m_lock)
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

	public void Reset()
	{
		lock (m_lock)
		{
			m_hasFrame = false;
			m_latestFrameId = 0;
		}
	}
}
