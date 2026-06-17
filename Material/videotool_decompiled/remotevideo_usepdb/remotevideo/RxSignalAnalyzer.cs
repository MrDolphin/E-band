using System;
using System.Numerics;

namespace remotevideo;

internal static class RxSignalAnalyzer
{
	private const double RfLoHz = 200000000.0;

	internal readonly record struct ToneResult(
		double BasebandFrequencyHz,
		double RfFrequencyHz,
		double Rms,
		double Peak,
		double PaprDb);

	internal readonly record struct ChirpResult(
		double StartRfHz,
		double EndRfHz,
		double BandwidthHz,
		double ActiveTimeSeconds,
		double IdleTimeSeconds,
		double PrtSeconds,
		double LinearityRmsHz,
		string Direction);

	internal readonly record struct ChirpTrace(
		double[] TimeUs,
		double[] RfMHz,
		double[] Magnitude,
		int ActivePointCount);

	public static ToneResult AnalyzeTone(byte[] iq)
	{
		Complex previous = ReadIq(iq, 0);
		Complex phaseSum = Complex.Zero;
		double powerSum = previous.Magnitude * previous.Magnitude;
		double peak = previous.Magnitude;
		int sampleCount = iq.Length / 4;

		for (int i = 1; i < sampleCount; i++)
		{
			Complex current = ReadIq(iq, i);
			phaseSum += current * Complex.Conjugate(previous);
			double magnitude = current.Magnitude;
			powerSum += magnitude * magnitude;
			peak = Math.Max(peak, magnitude);
			previous = current;
		}

		double basebandFrequency = Math.Atan2(phaseSum.Imaginary, phaseSum.Real) *
			WaveformGenerator.SampleRateHz / (2.0 * Math.PI);
		double rms = Math.Sqrt(powerSum / sampleCount);
		double paprDb = 20.0 * Math.Log10(Math.Max(peak / Math.Max(rms, 1e-12), 1e-12));
		return new ToneResult(
			basebandFrequency,
			RfLoHz + basebandFrequency,
			rms,
			peak,
			paprDb);
	}

	public static ChirpResult AnalyzeChirp(byte[] iq)
	{
		int sampleCount = iq.Length / 4;
		Complex[] samples = new Complex[sampleCount];
		double[] power = new double[sampleCount];
		double maxPower = 0.0;
		for (int i = 0; i < sampleCount; i++)
		{
			samples[i] = ReadIq(iq, i);
			power[i] = samples[i].Magnitude * samples[i].Magnitude;
			maxPower = Math.Max(maxPower, power[i]);
		}

		double idleThreshold = maxPower * 0.08;
		(int idleStart, int idleLength) = FindLongestCircularRun(power, idleThreshold);
		int activeStart = (idleStart + idleLength) % sampleCount;
		int activeLength = sampleCount - idleLength;
		if (activeLength < 16)
		{
			throw new InvalidOperationException("RX frame does not contain a usable active Chirp segment.");
		}

		double[] frequencies = new double[activeLength - 1];
		for (int i = 0; i < frequencies.Length; i++)
		{
			Complex first = samples[(activeStart + i) % sampleCount];
			Complex second = samples[(activeStart + i + 1) % sampleCount];
			Complex delta = second * Complex.Conjugate(first);
			frequencies[i] = Math.Atan2(delta.Imaginary, delta.Real) *
				WaveformGenerator.SampleRateHz / (2.0 * Math.PI);
		}

		int trim = Math.Min(
			(int)Math.Round(WaveformGenerator.FmcwRampTimeSeconds * WaveformGenerator.SampleRateHz),
			frequencies.Length / 10);
		int fitStart = trim;
		int fitCount = frequencies.Length - 2 * trim;
		if (fitCount < 8)
		{
			fitStart = 0;
			fitCount = frequencies.Length;
		}

		(double intercept, double slope, double rmsError) =
			FitLine(frequencies, fitStart, fitCount);
		double startBaseband = intercept;
		double endBaseband = intercept + slope * (fitCount - 1);
		string direction = endBaseband >= startBaseband ? "up" : "down";
		return new ChirpResult(
			RfLoHz + startBaseband,
			RfLoHz + endBaseband,
			Math.Abs(endBaseband - startBaseband),
			activeLength / WaveformGenerator.SampleRateHz,
			idleLength / WaveformGenerator.SampleRateHz,
			sampleCount / WaveformGenerator.SampleRateHz,
			rmsError,
			direction);
	}

	public static ChirpTrace BuildChirpTrace(byte[] iq, int displayPointCount = 800)
	{
		int sampleCount = iq.Length / 4;
		Complex[] samples = new Complex[sampleCount];
		double[] power = new double[sampleCount];
		double maxPower = 0.0;
		for (int i = 0; i < sampleCount; i++)
		{
			samples[i] = ReadIq(iq, i);
			power[i] = samples[i].Magnitude * samples[i].Magnitude;
			maxPower = Math.Max(maxPower, power[i]);
		}

		(int idleStart, int idleLength) = FindLongestCircularRun(power, maxPower * 0.08);
		int activeStart = (idleStart + idleLength) % sampleCount;
		int activeLength = sampleCount - idleLength;
		int pointCount = Math.Min(displayPointCount, sampleCount);
		int activePointCount = (int)Math.Round(pointCount * activeLength / (double)sampleCount);
		double[] timeUs = new double[pointCount];
		double[] rfMHz = new double[pointCount];
		double[] magnitude = new double[pointCount];

		for (int point = 0; point < pointCount; point++)
		{
			int alignedIndex = (int)((long)point * sampleCount / pointCount);
			int sampleIndex = (activeStart + alignedIndex) % sampleCount;
			int nextIndex = (sampleIndex + 1) % sampleCount;
			Complex delta = samples[nextIndex] * Complex.Conjugate(samples[sampleIndex]);
			double basebandHz = Math.Atan2(delta.Imaginary, delta.Real) *
				WaveformGenerator.SampleRateHz / (2.0 * Math.PI);

			timeUs[point] = alignedIndex * 1e6 / WaveformGenerator.SampleRateHz;
			rfMHz[point] = (RfLoHz + basebandHz) / 1e6;
			magnitude[point] = samples[sampleIndex].Magnitude;
		}

		return new ChirpTrace(timeUs, rfMHz, magnitude, activePointCount);
	}

	private static (int Start, int Length) FindLongestCircularRun(double[] values, double threshold)
	{
		int count = values.Length;
		int bestStart = 0;
		int bestLength = 0;
		int runStart = 0;
		int runLength = 0;

		for (int i = 0; i < count * 2; i++)
		{
			if (values[i % count] <= threshold && runLength < count)
			{
				if (runLength == 0)
				{
					runStart = i;
				}
				runLength++;
				if (runLength > bestLength)
				{
					bestStart = runStart % count;
					bestLength = runLength;
				}
			}
			else
			{
				runLength = 0;
			}
		}
		return (bestStart, bestLength);
	}

	private static (double Intercept, double Slope, double RmsError) FitLine(
		double[] values,
		int start,
		int count)
	{
		double sumX = 0.0;
		double sumY = 0.0;
		double sumXx = 0.0;
		double sumXy = 0.0;
		for (int i = 0; i < count; i++)
		{
			double y = values[start + i];
			sumX += i;
			sumY += y;
			sumXx += i * (double)i;
			sumXy += i * y;
		}

		double denominator = count * sumXx - sumX * sumX;
		double slope = denominator == 0.0 ? 0.0 :
			(count * sumXy - sumX * sumY) / denominator;
		double intercept = (sumY - slope * sumX) / count;
		double errorPower = 0.0;
		for (int i = 0; i < count; i++)
		{
			double error = values[start + i] - (intercept + slope * i);
			errorPower += error * error;
		}
		return (intercept, slope, Math.Sqrt(errorPower / count));
	}

	private static Complex ReadIq(byte[] data, int index)
	{
		int offset = index * 4;
		short i = (short)(data[offset] | data[offset + 1] << 8);
		short q = (short)(data[offset + 2] | data[offset + 3] << 8);
		return new Complex(i / 32768.0, q / 32768.0);
	}
}
