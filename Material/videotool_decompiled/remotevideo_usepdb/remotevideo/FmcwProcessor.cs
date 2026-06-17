using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;

namespace remotevideo;

internal static class FmcwProcessor
{
	public const int DefaultCpiChirpCount = 64;

	private const double SpeedOfLight = 299792458.0;

	private const double RfLoHz = 200000000.0;

	private const double LowPassCutoffHz = 2000000.0;

	private const int LowPassTapCount = 65;

	private const int MaxTargets = 5;

	private const double DetectionThresholdDb = 12.0;

	private const double LoopbackMaxRangeMeters = 100.0;

	private const double LoopbackMaxVelocityMetersPerSecond = 120.0;

	internal readonly record struct Target(
		double DistanceMeters,
		double VelocityMetersPerSecond,
		double PowerDb,
		double SnrDb,
		int RangeBin,
		int DopplerBin);

	internal readonly record struct RxDiagnostics(
		string SyncStatus,
		int SyncOffsetSamples,
		int IdleStartSample,
		int IdleSampleCount,
		double CorrelationScore,
		bool SweepLocked,
		string SweepDirection,
		double SweepStartRfHz,
		double SweepEndRfHz,
		double SweepBandwidthHz,
		double SweepActiveTimeSeconds,
		double SweepIdleTimeSeconds,
		double SweepLinearityRmsHz,
		double Rms,
		double Peak,
		double MeanI,
		double MeanQ,
		double ClippingPercent,
		double ZeroPercent);

	internal sealed class CpiResult
	{
		public required int CpiIndex { get; init; }

		public required int ChirpCount { get; init; }

		public required int SamplesPerChirp { get; init; }

		public required int ActiveSampleCount { get; init; }

		public required int RangeFftLength { get; init; }

		public required int DopplerFftLength { get; init; }

		public required double RangeResolutionMeters { get; init; }

		public required double VelocityResolutionMetersPerSecond { get; init; }

		public required double[] BeatTimeUs { get; init; }

		public required double[] BeatI { get; init; }

		public required double[] BeatQ { get; init; }

		public required double[] RangeMeters { get; init; }

		public required double[] RangeDb { get; init; }

		public required double[] DopplerVelocityMetersPerSecond { get; init; }

		public required double[,] RangeDopplerDb { get; init; }

		public required IReadOnlyList<Target> Targets { get; init; }

		public required RxDiagnostics Diagnostics { get; init; }
	}

	public static (double BeatFrequencyHz, double DistanceMeters, double PeakDb) Process(byte[] rxIq)
	{
		int sampleCount = rxIq.Length / 4;
		if (sampleCount < 2)
		{
			throw new ArgumentException("FMCW processing requires at least two IQ samples.", nameof(rxIq));
		}

		Complex[] beat = BuildFilteredBeat(rxIq, WaveformGenerator.GenerateFmcwChirp(sampleCount), sampleCount);
		int fftLength = NextPowerOfTwo(sampleCount);
		Complex[] spectrum = new Complex[fftLength];
		for (int i = 0; i < sampleCount; i++)
		{
			spectrum[i] = beat[i] * Hann(i, sampleCount);
		}
		Fft(spectrum);

		int peakIndex = 1;
		double peakPower = 0.0;
		for (int i = 1; i < fftLength; i++)
		{
			double power = spectrum[i].Magnitude * spectrum[i].Magnitude;
			if (power > peakPower)
			{
				peakPower = power;
				peakIndex = i;
			}
		}

		double signedBin = peakIndex <= fftLength / 2 ? peakIndex : peakIndex - fftLength;
		double beatFrequency = Math.Abs(signedBin * WaveformGenerator.SampleRateHz / fftLength);
		double chirpDuration = WaveformGenerator.GetFmcwChirpSamples(sampleCount) /
			WaveformGenerator.SampleRateHz;
		double slope = WaveformGenerator.FmcwBandwidthHz / chirpDuration;
		double distance = SpeedOfLight * beatFrequency / (2.0 * slope);
		double peakDb = 10.0 * Math.Log10(Math.Max(peakPower, 1e-20));
		return (beatFrequency, distance, peakDb);
	}

	public static CpiResult ProcessCpi(IReadOnlyList<byte[]> chirps, int cpiIndex)
	{
		if (chirps.Count == 0)
		{
			throw new ArgumentException("FMCW CPI requires at least one chirp.", nameof(chirps));
		}

		int samplesPerChirp = chirps[0].Length / 4;
		if (samplesPerChirp < 16)
		{
			throw new ArgumentException("FMCW chirps are too short.", nameof(chirps));
		}
		for (int i = 1; i < chirps.Count; i++)
		{
			if (chirps[i].Length / 4 != samplesPerChirp)
			{
				throw new ArgumentException("All FMCW chirps in a CPI must have the same length.", nameof(chirps));
			}
		}

		byte[] txTemplate = WaveformGenerator.GenerateFmcwChirp(samplesPerChirp);
		Complex[] txSamples = ReadIqFrame(txTemplate, samplesPerChirp);
		int activeSamples = WaveformGenerator.GetFmcwChirpSamples(samplesPerChirp);
		int rangeFftLength = NextPowerOfTwo(activeSamples);
		int rangeBinCount = rangeFftLength / 2;
		int dopplerFftLength = NextPowerOfTwo(chirps.Count);
		Complex[][] rangeSpectra = new Complex[chirps.Count][];
		double[] rangePower = new double[rangeBinCount];
		Complex[] firstBeat = Array.Empty<Complex>();
		RxDiagnostics diagnostics = AnalyzeAndAlign(chirps[0], samplesPerChirp, txSamples, activeSamples, out Complex[] firstAlignedRx);

		for (int chirp = 0; chirp < chirps.Count; chirp++)
		{
			Complex[] alignedRx;
			if (chirp == 0)
			{
				alignedRx = firstAlignedRx;
			}
			else
			{
				AnalyzeAndAlign(chirps[chirp], samplesPerChirp, txSamples, activeSamples, out alignedRx);
			}
			Complex[] beat = BuildFilteredBeat(alignedRx, txTemplate, samplesPerChirp);
			if (chirp == 0)
			{
				firstBeat = beat;
			}

			Complex[] spectrum = new Complex[rangeFftLength];
			for (int sample = 0; sample < activeSamples; sample++)
			{
				spectrum[sample] = beat[sample] * Hann(sample, activeSamples);
			}
			Fft(spectrum);
			rangeSpectra[chirp] = spectrum;
			for (int bin = 0; bin < rangeBinCount; bin++)
			{
				rangePower[bin] += spectrum[bin].Magnitude * spectrum[bin].Magnitude;
			}
		}

		double[,] rangeDopplerDb = new double[dopplerFftLength, rangeBinCount];
		double[] allPowerDb = new double[dopplerFftLength * Math.Max(1, rangeBinCount - 1)];
		int dbIndex = 0;
		for (int range = 0; range < rangeBinCount; range++)
		{
			Complex[] doppler = new Complex[dopplerFftLength];
			for (int chirp = 0; chirp < chirps.Count; chirp++)
			{
				doppler[chirp] = rangeSpectra[chirp][range] * Hann(chirp, chirps.Count);
			}
			Fft(doppler);
			for (int dopplerBin = 0; dopplerBin < dopplerFftLength; dopplerBin++)
			{
				int shiftedBin = (dopplerBin + dopplerFftLength / 2) % dopplerFftLength;
				double db = PowerToDb(doppler[shiftedBin].Magnitude * doppler[shiftedBin].Magnitude);
				rangeDopplerDb[dopplerBin, range] = db;
				if (range > 0)
				{
					allPowerDb[dbIndex++] = db;
				}
			}
		}

		double noiseFloorDb = Median(allPowerDb.AsSpan(0, dbIndex));
		double slope = GetChirpSlope(samplesPerChirp);
		double[] rangeMeters = BuildRangeAxis(rangeBinCount, rangeFftLength, slope);
		double[] velocityAxis = BuildVelocityAxis(dopplerFftLength, samplesPerChirp);
		List<Target> targets = DetectTargets(rangeDopplerDb, noiseFloorDb, rangeMeters, velocityAxis);
		double[] rangeDb = new double[rangeBinCount];
		for (int range = 0; range < rangeBinCount; range++)
		{
			rangeDb[range] = PowerToDb(rangePower[range] / chirps.Count);
		}

		targets = targets
			.Select(target => target with
			{
				DistanceMeters = rangeMeters[target.RangeBin],
				VelocityMetersPerSecond = velocityAxis[target.DopplerBin],
				SnrDb = target.PowerDb - noiseFloorDb,
			})
			.OrderBy(target => Math.Abs(target.VelocityMetersPerSecond))
			.ThenBy(target => target.DistanceMeters)
			.ThenByDescending(target => target.PowerDb)
			.Take(MaxTargets)
			.ToList();

		return new CpiResult
		{
			CpiIndex = cpiIndex,
			ChirpCount = chirps.Count,
			SamplesPerChirp = samplesPerChirp,
			ActiveSampleCount = activeSamples,
			RangeFftLength = rangeFftLength,
			DopplerFftLength = dopplerFftLength,
			RangeResolutionMeters = SpeedOfLight / (2.0 * WaveformGenerator.FmcwBandwidthHz),
			VelocityResolutionMetersPerSecond =
				SpeedOfLight / RfLoHz / (2.0 * samplesPerChirp / WaveformGenerator.SampleRateHz * dopplerFftLength),
			BeatTimeUs = BuildBeatTimeAxis(firstBeat.Length),
			BeatI = firstBeat.Select(value => value.Real).ToArray(),
			BeatQ = firstBeat.Select(value => value.Imaginary).ToArray(),
			RangeMeters = rangeMeters,
			RangeDb = rangeDb,
			DopplerVelocityMetersPerSecond = velocityAxis,
			RangeDopplerDb = rangeDopplerDb,
			Targets = targets,
			Diagnostics = diagnostics,
		};
	}

	private static Complex[] BuildFilteredBeat(byte[] rxIq, byte[] txIq, int sampleCount)
	{
		Complex[] rx = new Complex[sampleCount];
		for (int i = 0; i < sampleCount; i++)
		{
			rx[i] = ReadIq(rxIq, i);
		}
		return BuildFilteredBeat(rx, txIq, sampleCount);
	}

	private static Complex[] BuildFilteredBeat(Complex[] rx, byte[] txIq, int sampleCount)
	{
		Complex[] beat = new Complex[sampleCount];
		for (int i = 0; i < sampleCount; i++)
		{
			beat[i] = rx[i] * Complex.Conjugate(ReadIq(txIq, i));
		}
		return LowPassFilter(beat);
	}

	private static RxDiagnostics AnalyzeAndAlign(
		byte[] rxIq,
		int sampleCount,
		Complex[] txTemplate,
		int activeSamples,
		out Complex[] aligned)
	{
		Complex[] samples = new Complex[sampleCount];
		double[] power = new double[sampleCount];
		double powerSum = 0.0;
		double peak = 0.0;
		double meanI = 0.0;
		double meanQ = 0.0;
		int clipped = 0;
		int zero = 0;
		for (int i = 0; i < sampleCount; i++)
		{
			int offset = i * 4;
			short rawI = (short)(rxIq[offset] | rxIq[offset + 1] << 8);
			short rawQ = (short)(rxIq[offset + 2] | rxIq[offset + 3] << 8);
			Complex sample = new Complex(rawI / 32768.0, rawQ / 32768.0);
			samples[i] = sample;
			power[i] = sample.Magnitude * sample.Magnitude;
			powerSum += power[i];
			peak = Math.Max(peak, sample.Magnitude);
			meanI += sample.Real;
			meanQ += sample.Imaginary;
			if (Math.Abs(rawI) >= 32700 || Math.Abs(rawQ) >= 32700)
			{
				clipped++;
			}
			if (rawI == 0 && rawQ == 0)
			{
				zero++;
			}
		}

		meanI /= sampleCount;
		meanQ /= sampleCount;
		double rms = Math.Sqrt(powerSum / sampleCount);
		double maxPower = power.Length == 0 ? 0.0 : power.Max();
		(int idleStart, int idleLength) = FindLongestCircularRun(power, maxPower * 0.08);
		int idleActiveStart = (idleStart + idleLength) % sampleCount;
		(int correlationStart, double correlationScore) = FindBestTemplateOffset(samples, txTemplate, activeSamples);
		int activeStart = correlationScore >= 0.12 ? correlationStart : idleActiveStart;
		int expectedIdle = sampleCount - WaveformGenerator.GetFmcwChirpSamples(sampleCount);
		bool syncReliable =
			correlationScore >= 0.12 ||
			(expectedIdle > 0 &&
				idleLength >= expectedIdle * 0.45 &&
				idleLength <= expectedIdle * 1.8);
		RxSignalAnalyzer.ChirpResult sweep = default;
		bool sweepLocked = false;
		try
		{
			sweep = RxSignalAnalyzer.AnalyzeChirp(rxIq);
			sweepLocked = IsExpectedFmcwSweep(sweep);
		}
		catch
		{
			sweep = new RxSignalAnalyzer.ChirpResult(
				0.0,
				0.0,
				0.0,
				0.0,
				0.0,
				sampleCount / WaveformGenerator.SampleRateHz,
				double.PositiveInfinity,
				"unknown");
		}
		aligned = new Complex[sampleCount];
		for (int i = 0; i < sampleCount; i++)
		{
			aligned[i] = samples[(activeStart + i) % sampleCount];
		}

		string status = syncReliable ?
			"已找到空闲段并完成软件对齐" :
			"空闲段不明显，已按最强候选边界对齐";
		if (clipped > sampleCount * 0.01)
		{
			status += "；可能过载/削顶";
		}
		return new RxDiagnostics(
			status,
			activeStart,
			idleStart,
			idleLength,
			correlationScore,
			sweepLocked,
			sweep.Direction,
			sweep.StartRfHz,
			sweep.EndRfHz,
			sweep.BandwidthHz,
			sweep.ActiveTimeSeconds,
			sweep.IdleTimeSeconds,
			sweep.LinearityRmsHz,
			rms,
			peak,
			meanI,
			meanQ,
			100.0 * clipped / sampleCount,
			100.0 * zero / sampleCount);
	}

	private static bool IsExpectedFmcwSweep(RxSignalAnalyzer.ChirpResult sweep)
	{
		return string.Equals(sweep.Direction, "up", StringComparison.OrdinalIgnoreCase) &&
			sweep.BandwidthHz >= 16_000_000.0 &&
			sweep.BandwidthHz <= 24_000_000.0 &&
			sweep.ActiveTimeSeconds >= 160e-6 &&
			sweep.ActiveTimeSeconds <= 195e-6 &&
			sweep.IdleTimeSeconds >= 5e-6 &&
			sweep.IdleTimeSeconds <= 45e-6 &&
			sweep.LinearityRmsHz <= 600_000.0;
	}

	private static Complex[] ReadIqFrame(byte[] data, int sampleCount)
	{
		Complex[] samples = new Complex[sampleCount];
		for (int i = 0; i < sampleCount; i++)
		{
			samples[i] = ReadIq(data, i);
		}
		return samples;
	}

	private static (int Offset, double Score) FindBestTemplateOffset(
		Complex[] rx,
		Complex[] tx,
		int activeSamples)
	{
		int sampleCount = Math.Min(rx.Length, tx.Length);
		int compareCount = Math.Clamp(activeSamples, 32, sampleCount);
		int stride = Math.Max(1, sampleCount / 512);
		int bestOffset = 0;
		double bestScore = double.NegativeInfinity;

		for (int offset = 0; offset < sampleCount; offset += stride)
		{
			double score = CorrelateAt(rx, tx, offset, compareCount);
			if (score > bestScore)
			{
				bestScore = score;
				bestOffset = offset;
			}
		}

		int refineStart = Math.Max(0, bestOffset - stride);
		int refineEnd = Math.Min(sampleCount - 1, bestOffset + stride);
		for (int offset = refineStart; offset <= refineEnd; offset++)
		{
			double score = CorrelateAt(rx, tx, offset, compareCount);
			if (score > bestScore)
			{
				bestScore = score;
				bestOffset = offset;
			}
		}

		return (bestOffset, Math.Clamp(bestScore, 0.0, 1.0));
	}

	private static double CorrelateAt(Complex[] rx, Complex[] tx, int offset, int count)
	{
		Complex sum = Complex.Zero;
		double rxPower = 0.0;
		double txPower = 0.0;
		for (int i = 0; i < count; i++)
		{
			Complex rxSample = rx[(offset + i) % rx.Length];
			Complex txSample = tx[i % tx.Length];
			sum += rxSample * Complex.Conjugate(txSample);
			rxPower += rxSample.Magnitude * rxSample.Magnitude;
			txPower += txSample.Magnitude * txSample.Magnitude;
		}
		double denominator = Math.Sqrt(rxPower * txPower);
		return denominator <= 1e-20 ? 0.0 : sum.Magnitude / denominator;
	}

	private static Complex[] LowPassFilter(Complex[] input)
	{
		double[] taps = BuildLowPassTaps();
		Complex[] output = new Complex[input.Length];
		int half = taps.Length / 2;
		for (int i = 0; i < input.Length; i++)
		{
			Complex sum = Complex.Zero;
			for (int tap = 0; tap < taps.Length; tap++)
			{
				int source = i + tap - half;
				if ((uint)source < (uint)input.Length)
				{
					sum += input[source] * taps[tap];
				}
			}
			output[i] = sum;
		}
		return output;
	}

	private static double[] BuildLowPassTaps()
	{
		double[] taps = new double[LowPassTapCount];
		int middle = LowPassTapCount / 2;
		double normalizedCutoff = LowPassCutoffHz / WaveformGenerator.SampleRateHz;
		double sum = 0.0;
		for (int i = 0; i < taps.Length; i++)
		{
			int n = i - middle;
			double sinc = n == 0 ?
				2.0 * normalizedCutoff :
				Math.Sin(2.0 * Math.PI * normalizedCutoff * n) / (Math.PI * n);
			double window = 0.5 - 0.5 * Math.Cos(2.0 * Math.PI * i / (taps.Length - 1));
			taps[i] = sinc * window;
			sum += taps[i];
		}
		for (int i = 0; i < taps.Length; i++)
		{
			taps[i] /= sum;
		}
		return taps;
	}

	private static List<Target> DetectTargets(
		double[,] rangeDopplerDb,
		double noiseFloorDb,
		double[] rangeMeters,
		double[] velocityAxis)
	{
		List<Target> loopbackTargets = DetectTargets(
			rangeDopplerDb,
			noiseFloorDb,
			rangeMeters,
			velocityAxis,
			LoopbackMaxRangeMeters,
			LoopbackMaxVelocityMetersPerSecond);
		return loopbackTargets.Count > 0 ?
			loopbackTargets
				.OrderBy(target => Math.Abs(target.VelocityMetersPerSecond))
				.ThenBy(target => target.DistanceMeters)
				.ThenByDescending(target => target.PowerDb)
				.Take(MaxTargets)
				.ToList() :
			DetectTargets(rangeDopplerDb, noiseFloorDb, rangeMeters, velocityAxis, double.MaxValue, double.MaxValue);
	}

	private static List<Target> DetectTargets(
		double[,] rangeDopplerDb,
		double noiseFloorDb,
		double[] rangeMeters,
		double[] velocityAxis,
		double maxRangeMeters,
		double maxVelocityMetersPerSecond)
	{
		int dopplerBins = rangeDopplerDb.GetLength(0);
		int rangeBins = rangeDopplerDb.GetLength(1);
		List<Target> candidates = new List<Target>();
		double threshold = noiseFloorDb + DetectionThresholdDb;
		for (int doppler = 0; doppler < dopplerBins; doppler++)
		{
			if (Math.Abs(velocityAxis[doppler]) > maxVelocityMetersPerSecond)
			{
				continue;
			}
			for (int range = 0; range < rangeBins; range++)
			{
				if (rangeMeters[range] > maxRangeMeters)
				{
					break;
				}
				double power = rangeDopplerDb[doppler, range];
				if (power < threshold)
				{
					continue;
				}
				bool localMaximum = true;
				for (int dy = -1; dy <= 1 && localMaximum; dy++)
				{
					int neighborDoppler = doppler + dy;
					if (neighborDoppler < 0 || neighborDoppler >= dopplerBins)
					{
						continue;
					}
					for (int dx = -1; dx <= 1; dx++)
					{
						int neighborRange = range + dx;
						if (neighborRange < 0 || neighborRange >= rangeBins)
						{
							continue;
						}
						if ((dx != 0 || dy != 0) &&
							rangeDopplerDb[neighborDoppler, neighborRange] > power)
						{
							localMaximum = false;
							break;
						}
					}
				}
				if (localMaximum)
				{
					candidates.Add(new Target(0.0, 0.0, power, 0.0, range, doppler));
				}
			}
		}
		return candidates
			.OrderByDescending(target => target.PowerDb)
			.Take(MaxTargets)
			.ToList();
	}

	private static double[] BuildRangeAxis(int rangeBinCount, int rangeFftLength, double slope)
	{
		double[] axis = new double[rangeBinCount];
		for (int i = 0; i < axis.Length; i++)
		{
			double beatFrequency = i * WaveformGenerator.SampleRateHz / rangeFftLength;
			axis[i] = SpeedOfLight * beatFrequency / (2.0 * slope);
		}
		return axis;
	}

	private static double[] BuildVelocityAxis(int dopplerFftLength, int samplesPerChirp)
	{
		double[] axis = new double[dopplerFftLength];
		double prtSeconds = samplesPerChirp / WaveformGenerator.SampleRateHz;
		double wavelength = SpeedOfLight / RfLoHz;
		for (int i = 0; i < axis.Length; i++)
		{
			double shiftedBin = i - dopplerFftLength / 2;
			double dopplerFrequency = shiftedBin / (dopplerFftLength * prtSeconds);
			axis[i] = dopplerFrequency * wavelength / 2.0;
		}
		return axis;
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

	private static double[] BuildBeatTimeAxis(int sampleCount)
	{
		double[] axis = new double[sampleCount];
		for (int i = 0; i < axis.Length; i++)
		{
			axis[i] = i * 1e6 / WaveformGenerator.SampleRateHz;
		}
		return axis;
	}

	private static double GetChirpSlope(int samplesPerChirp)
	{
		double chirpDuration = WaveformGenerator.GetFmcwChirpSamples(samplesPerChirp) /
			WaveformGenerator.SampleRateHz;
		return WaveformGenerator.FmcwBandwidthHz / chirpDuration;
	}

	private static int NextPowerOfTwo(int value)
	{
		int result = 1;
		while (result < value)
		{
			result <<= 1;
		}
		return result;
	}

	private static double Hann(int index, int length)
	{
		if (length <= 1)
		{
			return 1.0;
		}
		return 0.5 - 0.5 * Math.Cos(2.0 * Math.PI * index / (length - 1));
	}

	private static double PowerToDb(double power)
	{
		return 10.0 * Math.Log10(Math.Max(power, 1e-24));
	}

	private static double Median(ReadOnlySpan<double> values)
	{
		if (values.Length == 0)
		{
			return -240.0;
		}
		double[] sorted = values.ToArray();
		Array.Sort(sorted);
		int middle = sorted.Length / 2;
		return sorted.Length % 2 == 0 ?
			(sorted[middle - 1] + sorted[middle]) * 0.5 :
			sorted[middle];
	}

	private static Complex ReadIq(byte[] data, int index)
	{
		int offset = index * 4;
		short i = (short)(data[offset] | data[offset + 1] << 8);
		short q = (short)(data[offset + 2] | data[offset + 3] << 8);
		return new Complex(i / 32768.0, q / 32768.0);
	}

	internal static void Fft(Complex[] data)
	{
		int length = data.Length;
		for (int i = 1, j = 0; i < length; i++)
		{
			int bit = length >> 1;
			for (; (j & bit) != 0; bit >>= 1)
			{
				j ^= bit;
			}
			j ^= bit;
			if (i < j)
			{
				(data[i], data[j]) = (data[j], data[i]);
			}
		}

		for (int size = 2; size <= length; size <<= 1)
		{
			double angle = -2.0 * Math.PI / size;
			Complex step = new Complex(Math.Cos(angle), Math.Sin(angle));
			for (int start = 0; start < length; start += size)
			{
				Complex phase = Complex.One;
				int half = size >> 1;
				for (int offset = 0; offset < half; offset++)
				{
					Complex even = data[start + offset];
					Complex odd = data[start + offset + half] * phase;
					data[start + offset] = even + odd;
					data[start + offset + half] = even - odd;
					phase *= step;
				}
			}
		}
	}
}
