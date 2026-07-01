using System.Numerics;
using remotevideo;

static void Require(bool condition, string message)
{
	if (!condition)
	{
		throw new InvalidOperationException(message);
	}
}

static Complex[] AddChannelEffects(
	ReadOnlySpan<Complex> source,
	int prefixSamples,
	double phaseRadians,
	double amplitude,
	double noiseSigma,
	int seed,
	double frequencyOffsetRadiansPerSample = 0.0)
{
	Random random = new Random(seed);
	Complex[] result = new Complex[prefixSamples + source.Length + 19];
	for (int i = 0; i < result.Length; i++)
	{
		double noiseI = NextGaussian(random) * noiseSigma;
		double noiseQ = NextGaussian(random) * noiseSigma;
		result[i] = new Complex(noiseI, noiseQ);
	}
	for (int i = 0; i < source.Length; i++)
	{
		Complex rotation = Complex.FromPolarCoordinates(
			amplitude,
			phaseRadians + frequencyOffsetRadiansPerSample * i);
		result[prefixSamples + i] += source[i] * rotation;
	}
	return result;
}

static double NextGaussian(Random random)
{
	double u1 = Math.Max(random.NextDouble(), 1e-12);
	double u2 = random.NextDouble();
	return Math.Sqrt(-2.0 * Math.Log(u1)) * Math.Cos(2.0 * Math.PI * u2);
}

static IReadOnlyList<TxPacket> LoadTxManifest(string path)
{
	List<TxPacket> packets = new();
	using FileStream stream = new FileStream(
		path,
		FileMode.Open,
		FileAccess.Read,
		FileShare.ReadWrite);
	using StreamReader reader = new StreamReader(stream);
	reader.ReadLine();
	while (reader.ReadLine() is string line)
	{
		if (string.IsNullOrWhiteSpace(line))
		{
			continue;
		}
		string[] parts = line.Split(',');
		if (parts.Length < 12)
		{
			continue;
		}
		packets.Add(new TxPacket(
			uint.Parse(parts[2]),
			ushort.Parse(parts[5]),
			ushort.Parse(parts[6]),
			ushort.Parse(parts[7]),
			Convert.FromHexString(parts[11])));
	}
	return packets;
}

static void DecodeCapture(string path, string txManifestPath = null)
{
	byte[] capture;
	using (FileStream stream = new FileStream(
		path,
		FileMode.Open,
		FileAccess.Read,
		FileShare.ReadWrite))
	{
		capture = new byte[stream.Length];
		stream.ReadExactly(capture);
	}
	IReadOnlyList<TxPacket> txPackets = string.IsNullOrWhiteSpace(txManifestPath)
		? Array.Empty<TxPacket>()
		: LoadTxManifest(txManifestPath);
	if (txPackets.Count > 0)
	{
		Console.WriteLine($"TX manifest packets: {txPackets.Count}");
	}
	QpskStreamDecoder decoder = new();
	int decodedFrames = 0;
	int validWirelessFrames = 0;
	int invalidWirelessFrames = 0;
	Dictionary<string, int> invalidReasons = new();
	Dictionary<string, int> invalidHeaders = new();
	double bestCorrelation = 0.0;
	const int samplesPerChunk = 4096;
	int bytesPerChunk = samplesPerChunk * 4;

	for (int offset = 0; offset < capture.Length; offset += bytesPerChunk)
	{
		int count = Math.Min(bytesPerChunk, capture.Length - offset);
		count -= count % 4;
		decoder.AppendInt16Iq(capture.AsSpan(offset, count));
		while (decoder.TryReadFrame(out byte[] frame, out double correlation))
		{
			decodedFrames++;
			bestCorrelation = Math.Max(bestCorrelation, correlation);
			string symbolQuality = FormatSymbolQuality(QpskModem.LastDiagnostics);
			if (WirelessVideoFrame.TryParse(frame, out WirelessVideoFrame wireless))
			{
				validWirelessFrames++;
				Console.WriteLine(
					$"Valid wireless frame: video={wireless.FrameId}, " +
					$"chunk={wireless.ChunkIndex + 1}/{wireless.ChunkCount}, " +
					$"payload={wireless.Payload.Length}, corr={correlation:F4}");
			}
			else
			{
				invalidWirelessFrames++;
				string detail = DescribeWirelessParseFailure(frame);
				string reason = ClassifyWirelessParseFailure(frame);
				invalidReasons[reason] = invalidReasons.TryGetValue(reason, out int reasonCount)
					? reasonCount + 1
					: 1;
				string key = BuildWirelessHeaderKey(frame);
				invalidHeaders[key] = invalidHeaders.TryGetValue(key, out int headerCount)
					? headerCount + 1
					: 1;
				Console.WriteLine(
					$"Invalid wireless frame: bytes={frame.Length}, " +
					$"corr={correlation:F4}, {detail}");
				string comparison = CompareWithTxManifest(frame, txPackets);
				if (!string.IsNullOrEmpty(comparison))
				{
					Console.WriteLine($"  TX compare: {comparison}");
				}
				if (!string.IsNullOrEmpty(symbolQuality))
				{
					Console.WriteLine($"  Symbol quality: {symbolQuality}");
				}
				Console.WriteLine(
					$"  Head: {BitConverter.ToString(frame.Take(Math.Min(32, frame.Length)).ToArray())}");
			}
		}
		bestCorrelation = Math.Max(bestCorrelation, decoder.LastCorrelation);
	}

	Console.WriteLine($"Capture: {path}");
	Console.WriteLine($"Samples: {capture.Length / 4}");
	Console.WriteLine($"Decoded modem frames: {decodedFrames}");
	Console.WriteLine($"Valid wireless frames: {validWirelessFrames}");
	Console.WriteLine($"Invalid wireless frames: {invalidWirelessFrames}");
	if (invalidReasons.Count > 0)
	{
		Console.WriteLine("Invalid reason summary:");
		foreach (var pair in invalidReasons.OrderByDescending(pair => pair.Value))
		{
			Console.WriteLine($"  {pair.Key}: {pair.Value}");
		}
	}
	if (invalidHeaders.Count > 0)
	{
		Console.WriteLine("Invalid header repeat summary:");
		foreach (var pair in invalidHeaders
			.OrderByDescending(pair => pair.Value)
			.ThenBy(pair => pair.Key)
			.Take(12))
		{
			Console.WriteLine($"  {pair.Key}: {pair.Value}");
		}
	}
	Console.WriteLine($"Best preamble correlation: {bestCorrelation:F4}");
	Console.WriteLine($"Buffered samples: {decoder.BufferedSamples}");
}

static string FormatSymbolQuality(QpskDemodulationDiagnostics diagnostics)
{
	if (diagnostics == null || diagnostics.Segments.Count == 0)
	{
		return "";
	}
	return string.Join(
		"; ",
		diagnostics.Segments.Select(segment =>
			$"{segment.Index * 16}-{segment.Index * 16 + 15}:" +
			$"mag={segment.AverageMagnitude:F4}," +
			$"margin={segment.AverageDecisionMargin:F3}," +
			$"phase_rms={segment.PhaseErrorRms * 180.0 / Math.PI:F1}deg," +
			$"phase_change={segment.TrackedPhaseChange * 180.0 / Math.PI:+0.0;-0.0;0.0}deg"));
}

static string CompareWithTxManifest(byte[] rxFrame, IReadOnlyList<TxPacket> txPackets)
{
	if (txPackets.Count == 0 || rxFrame.Length < WirelessVideoFrame.HeaderSize)
	{
		return "";
	}
	if (BitConverter.ToUInt32(rxFrame, 0) != WirelessVideoFrame.SyncWord ||
		rxFrame[4] != WirelessVideoFrame.Version)
	{
		return "";
	}
	uint frameId = BitConverter.ToUInt32(rxFrame, 6);
	ushort chunkIndex = BitConverter.ToUInt16(rxFrame, 10);
	ushort chunkCount = BitConverter.ToUInt16(rxFrame, 12);
	ushort payloadLength = BitConverter.ToUInt16(rxFrame, 14);
	IEnumerable<TxPacket> candidates = txPackets.Where(packet =>
		packet.FrameId == frameId &&
		packet.ChunkIndex == chunkIndex &&
		packet.ChunkCount == chunkCount);
	if (payloadLength <= WirelessVideoFrame.MaxPayloadSize)
	{
		IEnumerable<TxPacket> exactPayload = candidates.Where(packet =>
			packet.PayloadLength == payloadLength);
		if (exactPayload.Any())
		{
			candidates = exactPayload;
		}
	}
	TxPacket best = null;
	int bestBitErrors = int.MaxValue;
	int bestByteErrors = int.MaxValue;
	int bestComparedBytes = 0;
	foreach (TxPacket candidate in candidates)
	{
		int comparedBytes = Math.Min(rxFrame.Length, candidate.Bytes.Length);
		int bitErrors = 0;
		int byteErrors = Math.Abs(rxFrame.Length - candidate.Bytes.Length);
		for (int index = 0; index < comparedBytes; index++)
		{
			byte delta = (byte)(rxFrame[index] ^ candidate.Bytes[index]);
			if (delta != 0)
			{
				byteErrors++;
				bitErrors += BitOperations.PopCount(delta);
			}
		}
		bitErrors += Math.Abs(rxFrame.Length - candidate.Bytes.Length) * 8;
		if (bitErrors < bestBitErrors)
		{
			best = candidate;
			bestBitErrors = bitErrors;
			bestByteErrors = byteErrors;
			bestComparedBytes = comparedBytes;
		}
	}
	if (best == null)
	{
		return $"no TX match for frame={frameId}, chunk={chunkIndex + 1}/{chunkCount}, payload={payloadLength}";
	}
	List<int> firstMismatches = new();
	List<int> payloadSegmentBits = new();
	int headerBitErrors = 0;
	int payloadBitErrors = 0;
	int crcBitErrors = 0;
	for (int index = 0; index < bestComparedBytes; index++)
	{
		byte delta = (byte)(rxFrame[index] ^ best.Bytes[index]);
		if (delta == 0)
		{
			continue;
		}
		if (firstMismatches.Count < 8)
		{
			firstMismatches.Add(index);
		}
		int bits = BitOperations.PopCount(delta);
		if (index < WirelessVideoFrame.HeaderSize)
		{
			headerBitErrors += bits;
		}
		else if (index >= best.Bytes.Length - WirelessVideoFrame.CrcSize)
		{
			crcBitErrors += bits;
		}
		else
		{
			payloadBitErrors += bits;
			int segment = (index - WirelessVideoFrame.HeaderSize) / 16;
			while (payloadSegmentBits.Count <= segment)
			{
				payloadSegmentBits.Add(0);
			}
			payloadSegmentBits[segment] += bits;
		}
	}
	string segmentSummary = string.Join(
		";",
		payloadSegmentBits.Select((bits, index) =>
			$"{index * 16}-{index * 16 + 15}:{bits}"));
	return
		$"bit_errors={bestBitErrors}, byte_errors={bestByteErrors}, " +
		$"compared={bestComparedBytes}/{best.Bytes.Length}, " +
		$"header_bits={headerBitErrors}, payload_bits={payloadBitErrors}, " +
		$"crc_bits={crcBitErrors}, first_mismatch=[{string.Join(",", firstMismatches)}], " +
		$"payload_segments={segmentSummary}";
}

static string ClassifyWirelessParseFailure(byte[] data)
{
	if (data.Length < WirelessVideoFrame.HeaderSize + WirelessVideoFrame.CrcSize)
	{
		return "too short for wireless header";
	}
	uint sync = BitConverter.ToUInt32(data, 0);
	if (sync != WirelessVideoFrame.SyncWord)
	{
		return "sync mismatch";
	}
	if (data[4] != WirelessVideoFrame.Version)
	{
		return "version mismatch";
	}
	ushort chunkIndex = BitConverter.ToUInt16(data, 10);
	ushort chunkCount = BitConverter.ToUInt16(data, 12);
	ushort payloadLength = BitConverter.ToUInt16(data, 14);
	int expectedLength = WirelessVideoFrame.HeaderSize +
		payloadLength +
		WirelessVideoFrame.CrcSize;
	if (payloadLength > WirelessVideoFrame.MaxPayloadSize)
	{
		return "payload too large";
	}
	if (data.Length != expectedLength)
	{
		return "length mismatch";
	}
	if (chunkCount == 0 || chunkIndex >= chunkCount)
	{
		return "chunk index/count mismatch";
	}
	return "CRC mismatch";
}

static string DescribeWirelessParseFailure(byte[] data)
{
	if (data.Length < WirelessVideoFrame.HeaderSize + WirelessVideoFrame.CrcSize)
	{
		return "too short for wireless header";
	}
	uint sync = BitConverter.ToUInt32(data, 0);
	if (sync != WirelessVideoFrame.SyncWord)
	{
		return $"sync mismatch 0x{sync:X8}";
	}
	if (data[4] != WirelessVideoFrame.Version)
	{
		return $"version mismatch {data[4]}";
	}
	byte payloadType = data[5];
	uint frameId = BitConverter.ToUInt32(data, 6);
	ushort chunkIndex = BitConverter.ToUInt16(data, 10);
	ushort chunkCount = BitConverter.ToUInt16(data, 12);
	ushort payloadLength = BitConverter.ToUInt16(data, 14);
	uint timestampMs = BitConverter.ToUInt32(data, 16);
	int expectedLength = WirelessVideoFrame.HeaderSize +
		payloadLength +
		WirelessVideoFrame.CrcSize;
	string header =
		$"type={payloadType}, frame={frameId}, " +
		$"chunk={chunkIndex + 1}/{chunkCount}, payload={payloadLength}, " +
		$"timestamp={timestampMs}";
	if (payloadLength > WirelessVideoFrame.MaxPayloadSize)
	{
		return $"payload too large {payloadLength} | {header}";
	}
	if (data.Length != expectedLength)
	{
		return $"length mismatch actual={data.Length}, expected={expectedLength} | {header}";
	}
	if (chunkCount == 0 || chunkIndex >= chunkCount)
	{
		return $"chunk index/count mismatch | {header}";
	}
	uint receivedCrc = BitConverter.ToUInt32(
		data,
		data.Length - WirelessVideoFrame.CrcSize);
	uint computedCrc = Crc32.Compute(
		data.AsSpan(0, data.Length - WirelessVideoFrame.CrcSize));
	if (receivedCrc != computedCrc)
	{
		return
			$"CRC mismatch rx=0x{receivedCrc:X8}, calc=0x{computedCrc:X8}, " +
			$"xor=0x{receivedCrc ^ computedCrc:X8} | {header}";
	}
	return $"unknown parse failure | {header}";
}

static string BuildWirelessHeaderKey(byte[] data)
{
	if (data.Length < WirelessVideoFrame.HeaderSize)
	{
		return "short-header";
	}
	uint sync = BitConverter.ToUInt32(data, 0);
	if (sync != WirelessVideoFrame.SyncWord || data[4] != WirelessVideoFrame.Version)
	{
		return $"sync=0x{sync:X8}, version={data[4]}";
	}
	uint frameId = BitConverter.ToUInt32(data, 6);
	ushort chunkIndex = BitConverter.ToUInt16(data, 10);
	ushort chunkCount = BitConverter.ToUInt16(data, 12);
	ushort payloadLength = BitConverter.ToUInt16(data, 14);
	return $"frame={frameId}, chunk={chunkIndex + 1}/{chunkCount}, payload={payloadLength}";
}

static Complex ReadInt16Iq(byte[] data, int index)
{
	int offset = index * 4;
	short i = (short)(data[offset] | data[offset + 1] << 8);
	short q = (short)(data[offset + 2] | data[offset + 3] << 8);
	return new Complex(i / 32768.0, q / 32768.0);
}

static void WriteInt16Iq(byte[] data, int index, Complex value)
{
	int offset = index * 4;
	short i = (short)Math.Clamp(Math.Round(value.Real * 24000.0), short.MinValue, short.MaxValue);
	short q = (short)Math.Clamp(Math.Round(value.Imaginary * 24000.0), short.MinValue, short.MaxValue);
	data[offset] = (byte)(i & 0xFF);
	data[offset + 1] = (byte)((i >> 8) & 0xFF);
	data[offset + 2] = (byte)(q & 0xFF);
	data[offset + 3] = (byte)((q >> 8) & 0xFF);
}

static IReadOnlyList<byte[]> BuildSyntheticFmcwCpi(int rangeBin, int dopplerBin)
{
	const int sampleCount = 6144;
	byte[] tx = WaveformGenerator.GenerateFmcwChirp(sampleCount);
	List<byte[]> chirps = new();
	int activeSamples = WaveformGenerator.GetFmcwChirpSamples(sampleCount);
	int rangeFftLength = 1;
	while (rangeFftLength < activeSamples)
	{
		rangeFftLength <<= 1;
	}
	double beatFrequency = rangeBin * WaveformGenerator.SampleRateHz / rangeFftLength;
	double prtSeconds = sampleCount / WaveformGenerator.SampleRateHz;
	double dopplerFrequency = dopplerBin / (FmcwProcessor.DefaultCpiChirpCount * prtSeconds);
	for (int chirp = 0; chirp < FmcwProcessor.DefaultCpiChirpCount; chirp++)
	{
		byte[] rx = new byte[sampleCount * 4];
		double slowPhase = 2.0 * Math.PI * dopplerFrequency * prtSeconds * chirp;
		for (int sample = 0; sample < sampleCount; sample++)
		{
			double fastPhase = 2.0 * Math.PI * beatFrequency * sample / WaveformGenerator.SampleRateHz;
			Complex value = ReadInt16Iq(tx, sample) *
				Complex.FromPolarCoordinates(0.9, slowPhase + fastPhase);
			WriteInt16Iq(rx, sample, value);
		}
		chirps.Add(rx);
	}
	return chirps;
}

if (args.Length > 0)
{
	if (args.Length >= 4 &&
		int.TryParse(args[1], out int preambleThresholdPercent) &&
		int.TryParse(args[2], out int quickThresholdPercent) &&
		int.TryParse(args[3], out int searchStep))
	{
		QpskModem.ConfigureTuning(
			preambleThresholdPercent,
			quickThresholdPercent,
			searchStep);
		Console.WriteLine(
			$"QPSK tuning: preamble={QpskModem.PreambleThreshold:F2}, " +
			$"quick={QpskModem.QuickThreshold:F2}, step={QpskModem.PreambleSearchStep}");
	}
	string txManifestPath = args.Length >= 5 ? args[4] : null;
	if (args.Length >= 7 &&
		double.TryParse(args[5], out double phaseErrorGain) &&
		double.TryParse(args[6], out double phaseStepGain))
	{
		QpskModem.ConfigurePhaseTracking(phaseErrorGain, phaseStepGain);
	}
	Console.WriteLine(
		$"QPSK phase tracking: error_gain={QpskModem.PhaseErrorGain:G4}, " +
		$"step_gain={QpskModem.PhaseStepGain:G4}");
	DecodeCapture(args[0], txManifestPath);
	return;
}

Console.WriteLine("1. Wireless frame serialize/CRC...");
Mode9TuningProfile recommendedTuning = Mode9TuningProfile.Recommended;
Require(
	Mode9TuningProfile.MaxRepairRoundCount == 10,
	"Mode 9 repair-round limit changed.");
Require(
	recommendedTuning.RepairRoundCount == 1 &&
	recommendedTuning.RepairWaitMs == 100 &&
	recommendedTuning.QpskTxScalePercent == 65 &&
	recommendedTuning.PreambleThresholdPercent == 25 &&
	recommendedTuning.QuickThresholdPercent == 30 &&
	recommendedTuning.PreambleSearchStep == 4 &&
	recommendedTuning.WirelessChunkPayloadBytes == 32 &&
	recommendedTuning.IqCaptureLimitMb == 512 &&
	recommendedTuning.VideoTxMaxLongEdge == 240 &&
	recommendedTuning.VideoTxWebPQuality == 25,
	"Mode 9 recommended defaults changed.");
string tuningPath = Path.Combine(
	Path.GetTempPath(),
	$"remotevideo-mode9-{Guid.NewGuid():N}.json");
try
{
	recommendedTuning.Save(tuningPath);
	Mode9TuningProfile loadedTuning = Mode9TuningProfile.Load(tuningPath);
	Require(
		loadedTuning == recommendedTuning,
		"Mode 9 tuning profile did not survive a save/load round trip.");
	Mode9TuningProfile fiveRepairRounds = recommendedTuning with
	{
		RepairRoundCount = 5,
	};
	fiveRepairRounds.Save(tuningPath);
	Require(
		Mode9TuningProfile.Load(tuningPath).RepairRoundCount == 5,
		"Mode 9 tuning profile truncated five repair rounds.");
}
finally
{
	File.Delete(tuningPath);
}
Require(
	AppBuildInfo.BuildTitle("视频流测试工具", "1.1", "2b23a88") ==
	"视频流测试工具 v1.1 (2b23a88)",
	"Window title build/version format changed.");

byte[] chunkPayload = Enumerable.Range(0, 251).Select(i => (byte)(i * 17)).ToArray();
WirelessVideoFrame original = new(
	WirelessPayloadType.Video,
	42,
	2,
	7,
	123456,
	chunkPayload);
byte[] serialized = original.Serialize();
Require(WirelessVideoFrame.TryParse(serialized, out WirelessVideoFrame parsed), "Valid frame rejected.");
Require(parsed == original || parsed.Payload.SequenceEqual(original.Payload), "Frame fields changed.");
byte[] corrupted = serialized.ToArray();
corrupted[WirelessVideoFrame.HeaderSize + 3] ^= 0x20;
Require(!WirelessVideoFrame.TryParse(corrupted, out _), "CRC failed to reject corruption.");

Console.WriteLine("2. Video fragmentation/reassembly...");
byte[] videoPayload = new byte[8193];
new Random(1001).NextBytes(videoPayload);
IReadOnlyList<WirelessVideoFrame> fragments =
	WirelessVideoFrame.Fragment(videoPayload, 77, 5555, 700);
WirelessVideoReassembler reassembler = new();
byte[] completed = null;
foreach (WirelessVideoFrame fragment in fragments.Reverse())
{
	reassembler.TryAdd(fragment, out byte[] candidate);
	completed ??= candidate;
}
Require(completed != null && completed.SequenceEqual(videoPayload), "Video reassembly mismatch.");

Console.WriteLine("3. QPSK clean loopback with timing/phase offset...");
byte[] wirelessBytes = fragments[0].Serialize();
Complex[] tx = QpskModem.Modulate(wirelessBytes);
Complex[] cleanChannel = AddChannelEffects(tx, 37, 0.61, 0.72, 0.0, 10);
Require(
	QpskModem.TryDemodulate(cleanChannel, out byte[] cleanRx, out double cleanCorrelation),
	"Clean QPSK frame not detected.");
Require(cleanRx.SequenceEqual(wirelessBytes), "Clean QPSK payload mismatch.");
Require(
	QpskModem.LastDiagnostics != null &&
	QpskModem.LastDiagnostics.Segments.Count > 0,
	"Clean QPSK demodulation did not expose symbol diagnostics.");
Require(
	QpskModem.LastDiagnostics.Segments.All(segment =>
		segment.AverageMagnitude > 0.5 &&
		segment.AverageDecisionMargin > 0.5),
	"Clean QPSK symbol diagnostics reported unexpectedly poor quality.");
Require(cleanCorrelation > 0.99, "Clean preamble correlation is unexpectedly low.");

Console.WriteLine("4. QPSK AWGN loopback...");
Complex[] noisyChannel = AddChannelEffects(tx, 23, -0.43, 0.85, 0.10, 20);
Require(
	QpskModem.TryDemodulate(noisyChannel, out byte[] noisyRx, out double noisyCorrelation),
	"Noisy QPSK frame not detected.");
Require(noisyRx.SequenceEqual(wirelessBytes), "Noisy QPSK payload mismatch.");
Require(WirelessVideoFrame.TryParse(noisyRx, out WirelessVideoFrame noisyFrame), "Noisy frame CRC failed.");

Console.WriteLine("5. QPSK carrier-frequency offset correction...");
Complex[] offsetChannel = AddChannelEffects(
	tx,
	29,
	0.31,
	0.78,
	0.035,
	25,
	0.004);
Require(
	QpskModem.TryDemodulate(offsetChannel, out byte[] offsetRx, out double offsetCorrelation),
	"Frequency-offset QPSK frame not detected.");
Require(offsetRx.SequenceEqual(wirelessBytes), "Frequency-offset QPSK payload mismatch.");

Console.WriteLine("6. QPSK detection at every sample phase...");
for (int prefix = 32; prefix < 36; prefix++)
{
	Complex[] phaseChannel = AddChannelEffects(
		tx,
		prefix,
		-0.22,
		0.81,
		0.025,
		100 + prefix,
		0.0035);
	Require(
		QpskModem.TryDemodulate(phaseChannel, out byte[] phaseRx, out _),
		$"QPSK frame not detected at sample phase {prefix % QpskModem.SamplesPerSymbol}.");
	Require(
		phaseRx.SequenceEqual(wirelessBytes),
		$"QPSK payload mismatch at sample phase {prefix % QpskModem.SamplesPerSymbol}.");
}

Console.WriteLine("7. Streaming decoder with arbitrary RX chunk boundaries...");
short[] streamIq = QpskModem.ToInterleavedInt16(cleanChannel, 0.8);
byte[] streamBytes = new byte[streamIq.Length * sizeof(short)];
Buffer.BlockCopy(streamIq, 0, streamBytes, 0, streamBytes.Length);
QpskStreamDecoder streamDecoder = new();
byte[] streamedRx = null;
int byteOffset = 0;
Random chunkRandom = new Random(30);
while (byteOffset < streamBytes.Length)
{
	int sampleChunk = chunkRandom.Next(137, 1800);
	int byteCount = Math.Min(sampleChunk * 4, streamBytes.Length - byteOffset);
	byteCount -= byteCount % 4;
	if (byteCount == 0)
	{
		break;
	}
	streamDecoder.AppendInt16Iq(streamBytes.AsSpan(byteOffset, byteCount));
	byteOffset += byteCount;
	if (streamDecoder.TryReadFrame(out byte[] candidate, out _))
	{
		streamedRx = candidate;
		break;
	}
}
Require(streamedRx != null && streamedRx.SequenceEqual(wirelessBytes),
	"Streaming QPSK decoder failed across RX chunk boundaries.");
Require(streamDecoder.LastFrameStart >= 0, "Streaming decoder did not report frame start.");
Require(streamDecoder.LastConsumedSamples > 0, "Streaming decoder did not report consumed samples.");
Require(!streamDecoder.LastUsedConjugate, "Normal streaming decoder was incorrectly marked conjugated.");

Console.WriteLine("8. Streaming decoder with conjugated IQ...");
short[] conjugateIq = QpskModem.ToInterleavedInt16(
	cleanChannel.Select(Complex.Conjugate).ToArray(),
	0.8);
byte[] conjugateBytes = new byte[conjugateIq.Length * sizeof(short)];
Buffer.BlockCopy(conjugateIq, 0, conjugateBytes, 0, conjugateBytes.Length);
QpskStreamDecoder conjugateDecoder = new();
conjugateDecoder.AppendInt16Iq(conjugateBytes);
Require(
	conjugateDecoder.TryReadFrame(out byte[] conjugateRx, out _),
	"Conjugated IQ frame not detected.");
Require(conjugateRx.SequenceEqual(wirelessBytes), "Conjugated IQ payload mismatch.");
Require(conjugateDecoder.LastUsedConjugate, "Conjugated IQ path was not selected.");

Console.WriteLine("9. Scrambled repeat variants...");
for (byte scramblerId = 0; scramblerId < 5; scramblerId++)
{
	Complex[] scrambledTx = QpskModem.Modulate(wirelessBytes, scramblerId);
	Complex[] nextVariant = QpskModem.Modulate(wirelessBytes, (byte)(scramblerId + 1));
	Require(
		!scrambledTx.SequenceEqual(nextVariant),
		"Scrambler variants produced identical IQ.");
	Complex[] scrambledChannel = AddChannelEffects(
		scrambledTx,
		31 + scramblerId,
		0.27,
		0.82,
		0.04,
		200 + scramblerId,
		0.002);
	Require(
		QpskModem.TryDemodulate(scrambledChannel, out byte[] scrambledRx, out _),
		$"Scrambled QPSK variant {scramblerId} was not detected.");
	Require(
		scrambledRx.SequenceEqual(wirelessBytes),
		$"Scrambled QPSK variant {scramblerId} payload mismatch.");
}

Console.WriteLine("10. Short wireless chunks with redundant scrambled repeats...");
IReadOnlyList<WirelessVideoFrame> shortFragments =
	WirelessVideoFrame.Fragment(videoPayload, 88, 7777, 256);
WirelessVideoReassembler shortReassembler = new();
byte[] shortCompleted = null;
foreach (WirelessVideoFrame fragment in shortFragments)
{
	for (byte repeat = 0; repeat < 5; repeat++)
	{
		byte[] fragmentBytes = fragment.Serialize();
		byte scramblerId = (byte)(repeat * 53 + fragment.ChunkIndex * 17);
		Complex[] repeatTx = QpskModem.Modulate(fragmentBytes, scramblerId);
		Complex[] repeatRx = AddChannelEffects(
			repeatTx,
			17 + repeat,
			-0.18,
			0.8,
			0.045,
			5000 + fragment.ChunkIndex * 10 + repeat,
			0.0025);
		if (!QpskModem.TryDemodulate(repeatRx, out byte[] decodedRepeat, out _) ||
			!WirelessVideoFrame.TryParse(decodedRepeat, out WirelessVideoFrame decodedFragment))
		{
			continue;
		}
		if (shortReassembler.TryAdd(decodedFragment, out byte[] candidate))
		{
			shortCompleted = candidate;
		}
	}
}
Require(
	shortCompleted != null && shortCompleted.SequenceEqual(videoPayload),
	"Short redundant chunks failed to reconstruct the video payload.");

Console.WriteLine("11. FMCW CPI range-Doppler synthetic target...");
const int syntheticRangeBin = 8;
const int syntheticDopplerBin = 3;
IReadOnlyList<byte[]> fmcwCpi = BuildSyntheticFmcwCpi(syntheticRangeBin, syntheticDopplerBin);
var fmcwResult = FmcwProcessor.ProcessCpi(fmcwCpi, 1);
double expectedDistance = fmcwResult.RangeMeters[syntheticRangeBin];
if (fmcwResult.Targets.Count == 0)
{
	Console.WriteLine("FMCW diagnostic warning: synthetic target was not detected.");
}
else
{
	Console.WriteLine(string.Join("; ", fmcwResult.Targets.Select(target =>
		$"FMCW target R={target.DistanceMeters:F2}, V={target.VelocityMetersPerSecond:F2}, SNR={target.SnrDb:F1}")));
	if (!fmcwResult.Targets.Any(target =>
		Math.Abs(target.DistanceMeters - expectedDistance) <=
			fmcwResult.RangeResolutionMeters * 1.5))
	{
		Console.WriteLine($"FMCW diagnostic warning: expected R={expectedDistance:F2} was not the strongest target.");
	}
}

Console.WriteLine("12. Rotating/reversing repeat schedule...");
const int scheduledChunkCount = 48;
HashSet<int> edgeChunks = new();
for (int repeat = 0; repeat < 5; repeat++)
{
	int[] order = Enumerable.Range(0, scheduledChunkCount)
		.Select(position => WirelessChunkSchedule.GetIndex(
			scheduledChunkCount,
			repeat,
			position))
		.ToArray();
	Require(
		order.Distinct().Count() == scheduledChunkCount,
		$"Repeat schedule {repeat} contains duplicate or missing chunks.");
	edgeChunks.Add(order[0]);
	edgeChunks.Add(order[^1]);
}
Require(
	edgeChunks.Count >= 8,
	"Repeat schedule keeps too many chunks at vulnerable first/last positions.");

Console.WriteLine("12. Selective repair query...");
WirelessVideoReassembler repairReassembler = new();
IReadOnlyList<WirelessVideoFrame> repairFragments =
	WirelessVideoFrame.Fragment(videoPayload, 99, 8888, 256);
foreach (WirelessVideoFrame fragment in repairFragments
	.Where(fragment => fragment.ChunkIndex != 2 && fragment.ChunkIndex != 5))
{
	repairReassembler.TryAdd(fragment, out _);
}
Require(
	repairReassembler.TryGetMissingChunkIndices(
		99,
		out ushort[] missingRepairChunks,
		out bool repairCompleted),
	"Selective repair state was not available.");
Require(!repairCompleted, "Incomplete repair frame was marked complete.");
Require(
	missingRepairChunks.SequenceEqual(new ushort[] { 2, 5 }),
	"Selective repair returned the wrong missing chunks.");
foreach (ushort missingChunk in missingRepairChunks)
{
	repairReassembler.TryAdd(repairFragments[missingChunk], out _);
}
Require(
	repairReassembler.TryGetMissingChunkIndices(
		99,
		out missingRepairChunks,
		out repairCompleted) &&
	repairCompleted &&
	missingRepairChunks.Length == 0,
	"Selective repair did not report completion.");
repairReassembler.Clear();
Require(
	!repairReassembler.TryGetMissingChunkIndices(
		99,
		out _,
		out _),
	"Reassembler clear left stale repair state.");

Console.WriteLine("13. Two-pass delivery with selective repair...");
WirelessVideoReassembler twoPassReassembler = new();
IReadOnlyList<WirelessVideoFrame> twoPassFragments =
	WirelessVideoFrame.Fragment(videoPayload, 100, 9999, 256);
for (int repeat = 0; repeat < 2; repeat++)
{
	for (int position = 0; position < twoPassFragments.Count; position++)
	{
		int chunkIndex = WirelessChunkSchedule.GetIndex(
			twoPassFragments.Count,
			repeat,
			position);
		bool simulatedLoss =
			chunkIndex == 2 ||
			(repeat == 0 && chunkIndex % 7 == 0) ||
			(repeat == 1 && chunkIndex % 11 == 0);
		if (simulatedLoss)
		{
			continue;
		}
		twoPassReassembler.TryAdd(twoPassFragments[chunkIndex], out _);
	}
}
Require(
	twoPassReassembler.TryGetMissingChunkIndices(
		100,
		out ushort[] twoPassMissing,
		out bool twoPassCompleted),
	"Two-pass delivery did not create repair state.");
Require(!twoPassCompleted, "Two-pass loss simulation unexpectedly completed.");
Require(twoPassMissing.Length > 0, "Two-pass loss simulation had no missing chunks.");
byte[] twoPassCompletedPayload = null;
foreach (ushort missingChunk in twoPassMissing)
{
	if (twoPassReassembler.TryAdd(
		twoPassFragments[missingChunk],
		out byte[] candidate))
	{
		twoPassCompletedPayload = candidate;
	}
}
Require(
	twoPassCompletedPayload != null &&
	twoPassCompletedPayload.SequenceEqual(videoPayload),
	"Two-pass selective repair failed to reconstruct the video payload.");

Console.WriteLine("14. Still-image payload fragmentation and repair...");
byte[] photoPayload = new byte[14321];
new Random(2026).NextBytes(photoPayload);
IReadOnlyList<WirelessVideoFrame> photoFragments =
	WirelessVideoFrame.Fragment(photoPayload, 101, 10000, 256);
WirelessVideoReassembler photoReassembler = new();
byte[] photoCompletedPayload = null;
foreach (WirelessVideoFrame fragment in photoFragments
	.Where(fragment => fragment.ChunkIndex % 9 != 3))
{
	photoReassembler.TryAdd(fragment, out _);
}
Require(
	photoReassembler.TryGetMissingChunkIndices(
		101,
		out ushort[] photoMissingChunks,
		out bool photoCompleted),
	"Still-image repair state was not available.");
Require(!photoCompleted, "Incomplete still-image payload was marked complete.");
foreach (ushort missingChunk in photoMissingChunks)
{
	if (photoReassembler.TryAdd(
		photoFragments[missingChunk],
		out byte[] candidate))
	{
		photoCompletedPayload = candidate;
	}
}
Require(
	photoCompletedPayload != null &&
	photoCompletedPayload.SequenceEqual(photoPayload),
	"Still-image payload repair failed to reconstruct the payload.");

Console.WriteLine("15. Real-time video scheduling policy...");
Require(
	VideoRealtimePolicy.GetRepairChunkBudget(34) == 34,
	"Lossy links must allow one selective repair transmission per new chunk.");
Require(
	VideoRealtimePolicy.TargetFrameIntervalMs == 200,
	"Real-time video must target 5 FPS.");
Require(
	VideoRealtimePolicy.GetRepairChunkBudget(100) == 100,
	"Real-time repair budget did not scale with the new-frame chunk count.");
Require(
	!VideoRealtimePolicy.IsExpired(1000, 2999) &&
	VideoRealtimePolicy.IsExpired(1000, 3000),
	"Repair expiry must match the measured two-second recovery latency.");
Require(
	VideoRealtimePolicy.MaxEncodedBytes(32) == 1088,
	"Real-time WebP byte budget must cap a frame at 34 chunks.");

Console.WriteLine();
Console.WriteLine("All video modem self-tests passed.");
Console.WriteLine($"Fragments: {fragments.Count}");
Console.WriteLine($"QPSK samples for one {wirelessBytes.Length}-byte wireless frame: {tx.Length}");
Console.WriteLine($"Clean correlation: {cleanCorrelation:F4}");
Console.WriteLine($"Noisy correlation: {noisyCorrelation:F4}");
Console.WriteLine($"Frequency-offset correlation: {offsetCorrelation:F4}");

sealed record TxPacket(
	uint FrameId,
	ushort ChunkIndex,
	ushort ChunkCount,
	ushort PayloadLength,
	byte[] Bytes);
