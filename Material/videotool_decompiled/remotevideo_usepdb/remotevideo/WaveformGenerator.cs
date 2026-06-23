using System;

namespace remotevideo;

internal static class WaveformGenerator
{
	public const ushort SampleFormatInt16Iq = 1;

	private const short FullScale = 24000;

	private const short LowAmplitudeScale = 6000;

	private const short Qam16Low = 8000;

	private const short Qam16High = 24000;

	private const int PeriodicToneSamplesPerCycle = 32;

	public const double SampleRateHz = 30720000.0;

	public const double FmcwBandwidthHz = 20000000.0;

	public const double FmcwIdleTimeSeconds = 20e-6;

	public const double FmcwRampTimeSeconds = 2e-6;

	private const double TwoPi = 2.0 * Math.PI;

	public static byte[] GenerateInt16Iq(byte modulationMode, int sampleCount, int frameId)
	{
		if (sampleCount <= 0)
		{
			throw new ArgumentOutOfRangeException(nameof(sampleCount));
		}
		return modulationMode switch
		{
			1 => Generate16Qam(sampleCount, frameId),
			2 => GeneratePeriodicTone(sampleCount),
			3 => GenerateFmcwChirp(sampleCount),
			4 => GenerateCarrierTest(sampleCount),
			5 => GenerateComplexTone(sampleCount, -5000000.0),
			6 => GenerateComplexTone(sampleCount, 5000000.0),
			7 => GenerateTriangleFmcwChirp(sampleCount),
			10 => GenerateFmcwChirp(sampleCount),
			11 => GeneratePeriodicTone(sampleCount, LowAmplitudeScale),
			12 => GenerateComplexTone(sampleCount, -5000000.0, LowAmplitudeScale),
			13 => GenerateComplexTone(sampleCount, 5000000.0, LowAmplitudeScale),
			_ => GenerateQpsk(sampleCount, frameId),
		};
	}

	public static string GetModeName(byte modulationMode)
	{
		return modulationMode switch
		{
			1 => "16QAM",
			2 => "周期单音",
			3 => "FMCW Chirp",
			4 => "200 MHz CW test",
			5 => "195 MHz CW test",
			6 => "205 MHz CW test",
			7 => "Triangle FMCW Chirp",
			8 => "QPSK video software loopback",
			9 => "QPSK video E310 link",
			10 => "FMCW RX trace no mixer",
			11 => "Low amplitude 200.96 MHz tone",
			12 => "Low amplitude 195 MHz tone",
			13 => "Low amplitude 205 MHz tone",
			_ => "QPSK",
		};
	}

	public static byte[] GenerateFmcwChirp(int sampleCount)
	{
		byte[] result = new byte[sampleCount * 4];
		int idleSamples = Math.Min(sampleCount - 1, (int)Math.Round(FmcwIdleTimeSeconds * SampleRateHz));
		int chirpSamples = sampleCount - idleSamples;
		int rampSamples = Math.Min(chirpSamples / 2, (int)Math.Round(FmcwRampTimeSeconds * SampleRateHz));
		double startFrequency = -FmcwBandwidthHz / 2.0;
		double frequencyStep = FmcwBandwidthHz / Math.Max(1, chirpSamples - 1);
		double phase = 0.0;
		for (int i = 0; i < chirpSamples; i++)
		{
			double envelope = 1.0;
			if (rampSamples > 0 && i < rampSamples)
			{
				envelope = 0.5 - 0.5 * Math.Cos(Math.PI * i / rampSamples);
			}
			else if (rampSamples > 0 && i >= chirpSamples - rampSamples)
			{
				int remaining = chirpSamples - 1 - i;
				envelope = 0.5 - 0.5 * Math.Cos(Math.PI * remaining / rampSamples);
			}
			short iValue = (short)Math.Round(FullScale * envelope * Math.Cos(phase));
			short qValue = (short)Math.Round(FullScale * envelope * Math.Sin(phase));
			WriteInt16Iq(result, i, iValue, qValue);

			double instantaneousFrequency = startFrequency + (i + 0.5) * frequencyStep;
			phase += TwoPi * instantaneousFrequency / SampleRateHz;
			if (phase > Math.PI || phase < -Math.PI)
			{
				phase = Math.IEEERemainder(phase, TwoPi);
			}
		}
		return result;
	}

	public static int GetFmcwChirpSamples(int totalSamples)
	{
		int idleSamples = Math.Min(totalSamples - 1, (int)Math.Round(FmcwIdleTimeSeconds * SampleRateHz));
		return totalSamples - idleSamples;
	}

	private static byte[] GenerateTriangleFmcwChirp(int sampleCount)
	{
		byte[] result = new byte[sampleCount * 4];
		int idleSamples = Math.Min(sampleCount - 1, (int)Math.Round(FmcwIdleTimeSeconds * SampleRateHz));
		int chirpSamples = sampleCount - idleSamples;
		int rampSamples = Math.Min(chirpSamples / 4, (int)Math.Round(FmcwRampTimeSeconds * SampleRateHz));
		int upSamples = (chirpSamples + 1) / 2;
		int downSamples = chirpSamples - upSamples;
		double startFrequency = -FmcwBandwidthHz / 2.0;
		double endFrequency = FmcwBandwidthHz / 2.0;
		double phase = 0.0;

		for (int i = 0; i < chirpSamples; i++)
		{
			double envelope = 1.0;
			if (rampSamples > 0 && i < rampSamples)
			{
				envelope = 0.5 - 0.5 * Math.Cos(Math.PI * i / rampSamples);
			}
			else if (rampSamples > 0 && i >= chirpSamples - rampSamples)
			{
				int remaining = chirpSamples - 1 - i;
				envelope = 0.5 - 0.5 * Math.Cos(Math.PI * remaining / rampSamples);
			}

			short iValue = (short)Math.Round(FullScale * envelope * Math.Cos(phase));
			short qValue = (short)Math.Round(FullScale * envelope * Math.Sin(phase));
			WriteInt16Iq(result, i, iValue, qValue);

			double instantaneousFrequency;
			if (i < upSamples)
			{
				double fraction = upSamples <= 1 ? 0.0 : (double)i / (upSamples - 1);
				instantaneousFrequency = startFrequency +
					(endFrequency - startFrequency) * fraction;
			}
			else
			{
				int downIndex = i - upSamples;
				double fraction = downSamples <= 1 ? 1.0 : (double)downIndex / (downSamples - 1);
				instantaneousFrequency = endFrequency -
					(endFrequency - startFrequency) * fraction;
			}

			phase += TwoPi * instantaneousFrequency / SampleRateHz;
			if (phase > Math.PI || phase < -Math.PI)
			{
				phase = Math.IEEERemainder(phase, TwoPi);
			}
		}
		return result;
	}

	private static byte[] GeneratePeriodicTone(int sampleCount)
	{
		return GeneratePeriodicTone(sampleCount, FullScale);
	}

	private static byte[] GeneratePeriodicTone(int sampleCount, short amplitude)
	{
		byte[] result = new byte[sampleCount * 4];
		for (int i = 0; i < sampleCount; i++)
		{
			double phase = 2.0 * Math.PI * (i % PeriodicToneSamplesPerCycle) / PeriodicToneSamplesPerCycle;
			short iValue = (short)Math.Round(amplitude * Math.Cos(phase));
			short qValue = (short)Math.Round(amplitude * Math.Sin(phase));
			WriteInt16Iq(result, i, iValue, qValue);
		}
		return result;
	}

	private static byte[] GenerateCarrierTest(int sampleCount)
	{
		byte[] result = new byte[sampleCount * 4];
		for (int i = 0; i < sampleCount; i++)
		{
			WriteInt16Iq(result, i, FullScale, 0);
		}
		return result;
	}

	private static byte[] GenerateComplexTone(int sampleCount, double frequencyHz)
	{
		return GenerateComplexTone(sampleCount, frequencyHz, FullScale);
	}

	private static byte[] GenerateComplexTone(int sampleCount, double frequencyHz, short amplitude)
	{
		byte[] result = new byte[sampleCount * 4];
		double phase = 0.0;
		double phaseStep = TwoPi * frequencyHz / SampleRateHz;
		for (int i = 0; i < sampleCount; i++)
		{
			short iValue = (short)Math.Round(amplitude * Math.Cos(phase));
			short qValue = (short)Math.Round(amplitude * Math.Sin(phase));
			WriteInt16Iq(result, i, iValue, qValue);
			phase = Math.IEEERemainder(phase + phaseStep, TwoPi);
		}
		return result;
	}

	private static byte[] GenerateQpsk(int sampleCount, int frameId)
	{
		byte[] result = new byte[sampleCount * 4];
		uint state = Seed(frameId);
		for (int i = 0; i < sampleCount; i++)
		{
			byte bits = NextBits(ref state, 2);
			short iValue = ((bits & 1) == 0) ? FullScale : (short)(-FullScale);
			short qValue = ((bits & 2) == 0) ? FullScale : (short)(-FullScale);
			WriteInt16Iq(result, i, iValue, qValue);
		}
		return result;
	}

	private static byte[] Generate16Qam(int sampleCount, int frameId)
	{
		byte[] result = new byte[sampleCount * 4];
		uint state = Seed(frameId);
		for (int i = 0; i < sampleCount; i++)
		{
			byte bits = NextBits(ref state, 4);
			short iValue = Map16QamLevel(bits & 3);
			short qValue = Map16QamLevel((bits >> 2) & 3);
			WriteInt16Iq(result, i, iValue, qValue);
		}
		return result;
	}

	private static short Map16QamLevel(int bits)
	{
		return bits switch
		{
			0 => (short)(-Qam16High),
			1 => (short)(-Qam16Low),
			2 => Qam16Low,
			_ => Qam16High,
		};
	}

	private static void WriteInt16Iq(byte[] buffer, int sampleIndex, short iValue, short qValue)
	{
		int offset = sampleIndex * 4;
		buffer[offset] = (byte)(iValue & 0xFF);
		buffer[offset + 1] = (byte)((iValue >> 8) & 0xFF);
		buffer[offset + 2] = (byte)(qValue & 0xFF);
		buffer[offset + 3] = (byte)((qValue >> 8) & 0xFF);
	}

	private static uint Seed(int frameId)
	{
		return 0xA5A55A5Au ^ (uint)frameId * 2654435761u;
	}

	private static byte NextBits(ref uint state, int count)
	{
		uint value = 0;
		for (int i = 0; i < count; i++)
		{
			uint newBit = ((state >> 31) ^ (state >> 21) ^ (state >> 1) ^ state) & 1;
			state = (state << 1) | newBit;
			value |= (state & 1) << i;
		}
		return (byte)value;
	}
}
