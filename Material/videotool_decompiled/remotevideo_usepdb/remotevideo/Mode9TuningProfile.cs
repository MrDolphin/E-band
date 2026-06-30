using System;
using System.IO;
using System.Text.Json;

namespace remotevideo;

internal sealed record Mode9TuningProfile(
	int RepairRoundCount,
	int RepairWaitMs,
	int QpskTxScalePercent,
	int PreambleThresholdPercent,
	int QuickThresholdPercent,
	int PreambleSearchStep,
	int WirelessChunkPayloadBytes,
	int IqCaptureLimitMb,
	int VideoTxMaxLongEdge,
	int VideoTxWebPQuality)
{
	public const int MaxRepairRoundCount = 10;
	public const int DefaultVideoTxMaxLongEdge = 240;
	public const int DefaultVideoTxWebPQuality = 25;

	public static Mode9TuningProfile Recommended { get; } = new(
		1,
		100,
		65,
		25,
		30,
		4,
		32,
		512,
		DefaultVideoTxMaxLongEdge,
		DefaultVideoTxWebPQuality);

	public static string DefaultPath => Path.Combine(
		Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
		"E-band",
		"remotevideo",
		"mode9-tuning.json");

	public static Mode9TuningProfile Load(string path)
	{
		try
		{
			if (!File.Exists(path))
			{
				return Recommended;
			}
			return Normalize(
				JsonSerializer.Deserialize<Mode9TuningProfile>(
					File.ReadAllText(path)) ?? Recommended);
		}
		catch
		{
			return Recommended;
		}
	}

	public void Save(string path)
	{
		string directory = Path.GetDirectoryName(path);
		if (!string.IsNullOrEmpty(directory))
		{
			Directory.CreateDirectory(directory);
		}
		File.WriteAllText(
			path,
			JsonSerializer.Serialize(
				Normalize(this),
				new JsonSerializerOptions { WriteIndented = true }));
	}

	private static Mode9TuningProfile Normalize(Mode9TuningProfile value)
	{
		return new Mode9TuningProfile(
			Math.Clamp(value.RepairRoundCount, 0, MaxRepairRoundCount),
			Math.Clamp(value.RepairWaitMs, 0, 2000),
			Math.Clamp(value.QpskTxScalePercent, 5, 100),
			Math.Clamp(value.PreambleThresholdPercent, 25, 95),
			Math.Clamp(value.QuickThresholdPercent, 10, 90),
			Math.Clamp(value.PreambleSearchStep, 1, 4),
			Math.Clamp(value.WirelessChunkPayloadBytes, 32, WirelessVideoFrame.MaxPayloadSize),
			Math.Clamp(value.IqCaptureLimitMb, 16, 2048),
			Math.Clamp(
				value.VideoTxMaxLongEdge <= 0
					? DefaultVideoTxMaxLongEdge
					: value.VideoTxMaxLongEdge,
				64,
				1920),
			Math.Clamp(
				value.VideoTxWebPQuality <= 0
					? DefaultVideoTxWebPQuality
					: value.VideoTxWebPQuality,
				5,
				100));
	}
}
