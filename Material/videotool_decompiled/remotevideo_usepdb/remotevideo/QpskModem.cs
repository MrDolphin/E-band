using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Numerics;

namespace remotevideo;

internal static class QpskModem
{
	public const int SamplesPerSymbol = 4;
	public const int MaxFrameBytes = 4096;
	private const double SymbolScale = 0.7071067811865476;
	private static readonly byte[] PreambleBytes =
	{
		0xD3, 0x91, 0xC5, 0x6A, 0x3C, 0xA7, 0x5D, 0xE2,
		0x96, 0x4B, 0xF0, 0x1D, 0x87, 0xB2, 0x69, 0xCE,
	};
	private static readonly Complex[] PreambleSymbols = BytesToSymbols(PreambleBytes);
	private const byte ScrambledPacketMarker = 0xA7;
	private static int preambleThresholdPercent = 65;
	private static int quickThresholdPercent = 53;
	private static int preambleSearchStep = SamplesPerSymbol;

	public static double PreambleThreshold => ClampPercent(
		preambleThresholdPercent,
		25,
		95);

	public static double QuickThreshold => ClampPercent(
		quickThresholdPercent,
		10,
		90);

	public static int PreambleSearchStep => Math.Max(
		1,
		Math.Min(SamplesPerSymbol, preambleSearchStep));

	public static void ConfigureTuning(
		int preambleThreshold,
		int quickThreshold,
		int searchStep)
	{
		preambleThresholdPercent = preambleThreshold;
		quickThresholdPercent = quickThreshold;
		preambleSearchStep = searchStep;
	}

	public static Complex[] Modulate(ReadOnlySpan<byte> frame)
	{
		return Modulate(frame, 0);
	}

	public static Complex[] Modulate(ReadOnlySpan<byte> frame, byte scramblerId)
	{
		if (frame.Length == 0 || frame.Length > MaxFrameBytes)
		{
			throw new ArgumentOutOfRangeException(nameof(frame));
		}

		byte[] packet = new byte[6 + frame.Length];
		packet[0] = ScrambledPacketMarker;
		packet[1] = scramblerId;
		BinaryPrimitives.WriteUInt32LittleEndian(packet.AsSpan(2, 4), (uint)frame.Length);
		frame.CopyTo(packet.AsSpan(6));
		ApplyScrambler(packet.AsSpan(2), scramblerId);
		Complex[] payloadSymbols = BytesToSymbols(packet);
		Complex[] samples = new Complex[(PreambleSymbols.Length + payloadSymbols.Length) * SamplesPerSymbol];
		int output = 0;
		foreach (Complex symbol in PreambleSymbols)
		{
			for (int sample = 0; sample < SamplesPerSymbol; sample++)
			{
				samples[output++] = symbol;
			}
		}
		foreach (Complex symbol in payloadSymbols)
		{
			for (int sample = 0; sample < SamplesPerSymbol; sample++)
			{
				samples[output++] = symbol;
			}
		}
		return samples;
	}

	public static bool TryDemodulate(
		ReadOnlySpan<Complex> samples,
		out byte[] frame,
		out double correlation)
	{
		return TryDemodulate(
			samples,
			out frame,
			out correlation,
			out _,
			out _);
	}

	public static bool TryDemodulate(
		ReadOnlySpan<Complex> samples,
		out byte[] frame,
		out double correlation,
		out int frameStart,
		out int consumedSamples)
	{
		frame = null;
		correlation = 0.0;
		frameStart = -1;
		consumedSamples = 0;
		int minimumSamples = (PreambleSymbols.Length + 16) * SamplesPerSymbol;
		if (samples.Length < minimumSamples)
		{
			return false;
		}

		int bestStart = -1;
		Complex bestCorrelation = Complex.Zero;
		double bestScore = 0.0;
		double bestPhaseStep = 0.0;
		int searchLimit = samples.Length - minimumSamples;
		int searchStep = PreambleSearchStep;
		double quickThreshold = QuickThreshold;
		double preambleThreshold = PreambleThreshold;
		for (int start = 0; start <= searchLimit; start += searchStep)
		{
			double quickScore = EvaluatePreambleQuick(samples, start);
			if (quickScore < quickThreshold)
			{
				if (quickScore > bestScore)
				{
					bestScore = quickScore;
					bestStart = start;
				}
				continue;
			}
			EvaluatePreamble(
				samples,
				start,
				integrateSymbol: false,
				out Complex sum,
				out double score,
				out double phaseStep);
			if (score > bestScore)
			{
				bestScore = score;
				bestStart = start;
				bestCorrelation = sum;
				bestPhaseStep = phaseStep;
			}
		}

		if (bestStart >= 0)
		{
			int coarseStart = bestStart;
			int refineStart = Math.Max(0, coarseStart - (SamplesPerSymbol - 1));
			int refineEnd = Math.Min(searchLimit, coarseStart + (SamplesPerSymbol - 1));
			bestScore = -1.0;
			for (int start = refineStart; start <= refineEnd; start++)
			{
				EvaluatePreamble(
					samples,
					start,
					integrateSymbol: true,
					out Complex sum,
					out double score,
					out double phaseStep);
				if (score > bestScore)
				{
					bestScore = score;
					bestStart = start;
					bestCorrelation = sum;
					bestPhaseStep = phaseStep;
				}
			}
		}

		correlation = bestScore;
		frameStart = bestStart;
		if (bestStart < 0 || bestScore < preambleThreshold)
		{
			return false;
		}

		int payloadSampleStart = bestStart + PreambleSymbols.Length * SamplesPerSymbol;
		int availableSymbols = (samples.Length - payloadSampleStart) / SamplesPerSymbol;
		if (availableSymbols < 24)
		{
			return false;
		}

		byte[] packetHeader = DemodulateBytes(
			samples,
			payloadSampleStart,
			24,
			bestCorrelation.Phase,
			bestPhaseStep,
			PreambleSymbols.Length);
		if (packetHeader[0] != ScrambledPacketMarker)
		{
			return TryDemodulateLegacyPacket(
				samples,
				bestStart,
				payloadSampleStart,
				availableSymbols,
				bestCorrelation,
				bestPhaseStep,
				out frame,
				out consumedSamples);
		}

		byte scramblerId = packetHeader[1];
		ApplyScrambler(packetHeader.AsSpan(2), scramblerId);
		uint frameLength = BinaryPrimitives.ReadUInt32LittleEndian(packetHeader.AsSpan(2, 4));
		if (frameLength == 0 || frameLength > MaxFrameBytes)
		{
			return false;
		}

		int frameSymbols = checked((int)frameLength * 4);
		if (availableSymbols < 24 + frameSymbols)
		{
			return false;
		}
		frame = DemodulateBytes(
			samples,
			payloadSampleStart + 24 * SamplesPerSymbol,
			frameSymbols,
			bestCorrelation.Phase,
			bestPhaseStep,
			PreambleSymbols.Length + 24);
		ApplyScrambler(frame, scramblerId, 4);
		consumedSamples = bestStart +
			(PreambleSymbols.Length + 24 + frameSymbols) * SamplesPerSymbol;
		return true;
	}

	private static double ClampPercent(int value, int min, int max)
	{
		return Math.Max(min, Math.Min(max, value)) / 100.0;
	}

	private static bool TryDemodulateLegacyPacket(
		ReadOnlySpan<Complex> samples,
		int bestStart,
		int payloadSampleStart,
		int availableSymbols,
		Complex bestCorrelation,
		double bestPhaseStep,
		out byte[] frame,
		out int consumedSamples)
	{
		frame = null;
		consumedSamples = 0;
		byte[] lengthBytes = DemodulateBytes(
			samples,
			payloadSampleStart,
			16,
			bestCorrelation.Phase,
			bestPhaseStep,
			PreambleSymbols.Length);
		uint frameLength = BinaryPrimitives.ReadUInt32LittleEndian(lengthBytes);
		if (frameLength == 0 || frameLength > MaxFrameBytes)
		{
			return false;
		}

		int frameSymbols = checked((int)frameLength * 4);
		if (availableSymbols < 16 + frameSymbols)
		{
			return false;
		}
		frame = DemodulateBytes(
			samples,
			payloadSampleStart + 16 * SamplesPerSymbol,
			frameSymbols,
			bestCorrelation.Phase,
			bestPhaseStep,
			PreambleSymbols.Length + 16);
		consumedSamples = bestStart +
			(PreambleSymbols.Length + 16 + frameSymbols) * SamplesPerSymbol;
		return true;
	}

	private static void ApplyScrambler(Span<byte> data, byte scramblerId, int streamOffset = 0)
	{
		uint state = 0x9E3779B9u ^ ((uint)scramblerId + 1u) * 0x85EBCA6Bu;
		for (int index = 0; index < streamOffset + data.Length; index++)
		{
			state ^= state << 13;
			state ^= state >> 17;
			state ^= state << 5;
			if (index >= streamOffset)
			{
				data[index - streamOffset] ^= (byte)state;
			}
		}
	}

	public static short[] ToInterleavedInt16(ReadOnlySpan<Complex> samples, double amplitude = 0.7)
	{
		if (amplitude <= 0.0 || amplitude > 1.0)
		{
			throw new ArgumentOutOfRangeException(nameof(amplitude));
		}
		short[] iq = new short[samples.Length * 2];
		double scale = amplitude * short.MaxValue;
		for (int i = 0; i < samples.Length; i++)
		{
			iq[i * 2] = (short)Math.Clamp(
				(int)Math.Round(samples[i].Real * scale),
				short.MinValue,
				short.MaxValue);
			iq[i * 2 + 1] = (short)Math.Clamp(
				(int)Math.Round(samples[i].Imaginary * scale),
				short.MinValue,
				short.MaxValue);
		}
		return iq;
	}

	private static Complex[] BytesToSymbols(ReadOnlySpan<byte> bytes)
	{
		Complex[] symbols = new Complex[bytes.Length * 4];
		int output = 0;
		foreach (byte value in bytes)
		{
			for (int shift = 6; shift >= 0; shift -= 2)
			{
				symbols[output++] = MapDibit((value >> shift) & 0x03);
			}
		}
		return symbols;
	}

	private static byte[] DemodulateBytes(
		ReadOnlySpan<Complex> samples,
		int sampleStart,
		int symbolCount,
		double phase,
		double phaseStep,
		int firstSymbolIndex)
	{
		if (symbolCount % 4 != 0)
		{
			throw new InvalidDataException("QPSK symbol count must be byte aligned.");
		}
		byte[] bytes = new byte[symbolCount / 4];
		double trackedPhase = phase + phaseStep * firstSymbolIndex;
		double trackedPhaseStep = phaseStep;
		for (int symbol = 0; symbol < symbolCount; symbol++)
		{
			Complex value = AverageSymbol(samples, sampleStart, symbol);
			value *= Complex.FromPolarCoordinates(1.0, -trackedPhase);
			int dibit = DemapDibit(value);
			bytes[symbol / 4] |= (byte)(dibit << (6 - 2 * (symbol % 4)));
			Complex decision = MapDibit(dibit);
			double phaseError =
				(value * Complex.Conjugate(decision)).Phase;
			trackedPhaseStep += 0.0002 * phaseError;
			trackedPhase += trackedPhaseStep + 0.05 * phaseError;
		}
		return bytes;
	}

	private static Complex AverageSymbol(
		ReadOnlySpan<Complex> samples,
		int sampleStart,
		int symbol)
	{
		int offset = sampleStart + symbol * SamplesPerSymbol;
		Complex sum = Complex.Zero;
		for (int sample = 0; sample < SamplesPerSymbol; sample++)
		{
			sum += samples[offset + sample];
		}
		return sum / SamplesPerSymbol;
	}

	private static double EvaluatePreambleQuick(
		ReadOnlySpan<Complex> samples,
		int start)
	{
		const int quickSymbols = 16;
		Complex correlation = Complex.Zero;
		double samplePower = 0.0;
		for (int symbol = 0; symbol < quickSymbols; symbol++)
		{
			Complex received = samples[
				start +
				symbol * SamplesPerSymbol +
				SamplesPerSymbol / 2];
			correlation += received *
				Complex.Conjugate(PreambleSymbols[symbol]);
			samplePower += received.Magnitude * received.Magnitude;
		}
		return correlation.Magnitude /
			Math.Sqrt(Math.Max(samplePower * quickSymbols, 1e-12));
	}

	private static void EvaluatePreamble(
		ReadOnlySpan<Complex> samples,
		int start,
		bool integrateSymbol,
		out Complex correlation,
		out double score,
		out double phaseStep)
	{
		Complex phaseStepSum = Complex.Zero;
		Complex previousError = Complex.Zero;
		double samplePower = 0.0;

		for (int symbol = 0; symbol < PreambleSymbols.Length; symbol++)
		{
			Complex received = integrateSymbol
				? AverageSymbol(samples, start, symbol)
				: samples[
					start +
					symbol * SamplesPerSymbol +
					SamplesPerSymbol / 2];
			Complex error =
				received * Complex.Conjugate(PreambleSymbols[symbol]);
			if (symbol > 0)
			{
				phaseStepSum += error * Complex.Conjugate(previousError);
			}
			previousError = error;
			samplePower += received.Magnitude * received.Magnitude;
		}

		phaseStep = phaseStepSum.Phase;
		if (Math.Abs(phaseStep) < 0.003)
		{
			phaseStep = 0.0;
		}

		correlation = Complex.Zero;
		for (int symbol = 0; symbol < PreambleSymbols.Length; symbol++)
		{
			Complex received = integrateSymbol
				? AverageSymbol(samples, start, symbol)
				: samples[
					start +
					symbol * SamplesPerSymbol +
					SamplesPerSymbol / 2];
			Complex frequencyCorrection =
				Complex.FromPolarCoordinates(1.0, -phaseStep * symbol);
			correlation += received *
				Complex.Conjugate(PreambleSymbols[symbol]) *
				frequencyCorrection;
		}
		score = correlation.Magnitude /
			Math.Sqrt(Math.Max(samplePower * PreambleSymbols.Length, 1e-12));
	}

	private static Complex MapDibit(int dibit)
	{
		return dibit switch
		{
			0 => new Complex(SymbolScale, SymbolScale),
			1 => new Complex(-SymbolScale, SymbolScale),
			3 => new Complex(-SymbolScale, -SymbolScale),
			2 => new Complex(SymbolScale, -SymbolScale),
			_ => throw new ArgumentOutOfRangeException(nameof(dibit)),
		};
	}

	private static int DemapDibit(Complex symbol)
	{
		bool negativeI = symbol.Real < 0.0;
		bool negativeQ = symbol.Imaginary < 0.0;
		if (!negativeI && !negativeQ)
		{
			return 0;
		}
		if (negativeI && !negativeQ)
		{
			return 1;
		}
		if (negativeI && negativeQ)
		{
			return 3;
		}
		return 2;
	}
}
