using System;
using System.Collections.Generic;
using System.Numerics;

namespace remotevideo;

internal sealed class QpskStreamDecoder
{
	private const int MaximumBufferedSamples = 200000;
	private readonly List<Complex> samples = new();

	public int BufferedSamples => samples.Count;

	public double LastCorrelation { get; private set; }

	public double LastNormalCorrelation { get; private set; }

	public double LastConjugateCorrelation { get; private set; }

	public bool LastUsedConjugate { get; private set; }

	public int LastFrameStart { get; private set; } = -1;

	public int LastConsumedSamples { get; private set; }

	public void AppendInt16Iq(ReadOnlySpan<byte> iq)
	{
		int sampleCount = iq.Length / 4;
		if (samples.Capacity < samples.Count + sampleCount)
		{
			samples.Capacity = samples.Count + sampleCount;
		}
		for (int index = 0; index < sampleCount; index++)
		{
			int offset = index * 4;
			short i = (short)(iq[offset] | iq[offset + 1] << 8);
			short q = (short)(iq[offset + 2] | iq[offset + 3] << 8);
			samples.Add(new Complex(i / 32768.0, q / 32768.0));
		}
		TrimIfNeeded();
	}

	public bool TryReadFrame(out byte[] frame, out double correlation)
	{
		frame = null;
		correlation = 0.0;
		if (samples.Count == 0)
		{
			return false;
		}

		Complex[] snapshot = samples.ToArray();
		bool normalDecoded = QpskModem.TryDemodulate(
			snapshot,
			out frame,
			out double normalCorrelation,
			out int normalFrameStart,
			out int normalConsumedSamples);
		LastNormalCorrelation = normalCorrelation;

		bool conjugateDecoded = false;
		byte[] conjugateFrame = null;
		double conjugateCorrelation = 0.0;
		int conjugateFrameStart = -1;
		int conjugateConsumedSamples = 0;
		if (!normalDecoded)
		{
			for (int index = 0; index < snapshot.Length; index++)
			{
				snapshot[index] = Complex.Conjugate(snapshot[index]);
			}
			conjugateDecoded = QpskModem.TryDemodulate(
				snapshot,
				out conjugateFrame,
				out conjugateCorrelation,
				out conjugateFrameStart,
				out conjugateConsumedSamples);
		}
		LastConjugateCorrelation = conjugateCorrelation;

		if (!normalDecoded && !conjugateDecoded)
		{
			correlation = Math.Max(normalCorrelation, conjugateCorrelation);
			LastCorrelation = correlation;
			int frameStart = normalCorrelation >= conjugateCorrelation
				? normalFrameStart
				: conjugateFrameStart;
			if (correlation < QpskModem.PreambleThreshold && samples.Count > 4096)
			{
				samples.RemoveRange(0, samples.Count - 4096);
			}
			else if (frameStart > 4096)
			{
				samples.RemoveRange(0, frameStart);
			}
			return false;
		}

		int consumedSamples;
		if (conjugateDecoded)
		{
			frame = conjugateFrame;
			correlation = conjugateCorrelation;
			consumedSamples = conjugateConsumedSamples;
			LastFrameStart = conjugateFrameStart;
			LastUsedConjugate = true;
		}
		else
		{
			correlation = normalCorrelation;
			consumedSamples = normalConsumedSamples;
			LastFrameStart = normalFrameStart;
			LastUsedConjugate = false;
		}
		LastCorrelation = correlation;
		LastConsumedSamples = consumedSamples;
		samples.RemoveRange(0, Math.Min(consumedSamples, samples.Count));
		return true;
	}

	public void Clear()
	{
		samples.Clear();
		LastCorrelation = 0.0;
		LastNormalCorrelation = 0.0;
		LastConjugateCorrelation = 0.0;
		LastUsedConjugate = false;
		LastFrameStart = -1;
		LastConsumedSamples = 0;
	}

	private void TrimIfNeeded()
	{
		if (samples.Count <= MaximumBufferedSamples)
		{
			return;
		}
		samples.RemoveRange(0, samples.Count - MaximumBufferedSamples);
	}
}
