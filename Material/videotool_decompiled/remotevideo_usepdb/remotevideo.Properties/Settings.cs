using System.CodeDom.Compiler;
using System.Configuration;
using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace remotevideo.Properties;

[CompilerGenerated]
[GeneratedCode("Microsoft.VisualStudio.Editors.SettingsDesigner.SettingsSingleFileGenerator", "17.14.0.0")]
internal sealed class Settings : ApplicationSettingsBase
{
	private static Settings defaultInstance = (Settings)SettingsBase.Synchronized(new Settings());

	public static Settings Default => defaultInstance;

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("1")]
	public byte ProtocolVersion
	{
		get
		{
			return (byte)this["ProtocolVersion"];
		}
		set
		{
			this["ProtocolVersion"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("1")]
	public byte Mode
	{
		get
		{
			return (byte)this["Mode"];
		}
		set
		{
			this["Mode"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("3")]
	public byte CommMode
	{
		get
		{
			return (byte)this["CommMode"];
		}
		set
		{
			this["CommMode"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("0")]
	public byte RadarSignal
	{
		get
		{
			return (byte)this["RadarSignal"];
		}
		set
		{
			this["RadarSignal"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("100")]
	public byte RadarBandWidth
	{
		get
		{
			return (byte)this["RadarBandWidth"];
		}
		set
		{
			this["RadarBandWidth"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("100")]
	public short RadarTimeWidth
	{
		get
		{
			return (short)this["RadarTimeWidth"];
		}
		set
		{
			this["RadarTimeWidth"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("2")]
	public byte RadarAzimuthLooks
	{
		get
		{
			return (byte)this["RadarAzimuthLooks"];
		}
		set
		{
			this["RadarAzimuthLooks"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("50")]
	public int SendInterval
	{
		get
		{
			return (int)this["SendInterval"];
		}
		set
		{
			this["SendInterval"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("")]
	public string VideoFilePath
	{
		get
		{
			return (string)this["VideoFilePath"];
		}
		set
		{
			this["VideoFilePath"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("192.168.1.10")]
	public string RemoteEndAddr
	{
		get
		{
			return (string)this["RemoteEndAddr"];
		}
		set
		{
			this["RemoteEndAddr"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("8080")]
	public int RemoteEndPort
	{
		get
		{
			return (int)this["RemoteEndPort"];
		}
		set
		{
			this["RemoteEndPort"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("0")]
	public short Nd
	{
		get
		{
			return (short)this["Nd"];
		}
		set
		{
			this["Nd"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("0")]
	public short Nr
	{
		get
		{
			return (short)this["Nr"];
		}
		set
		{
			this["Nr"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("73")]
	public short Fc
	{
		get
		{
			return (short)this["Fc"];
		}
		set
		{
			this["Fc"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("0")]
	public byte RadarReturnDataMode
	{
		get
		{
			return (byte)this["RadarReturnDataMode"];
		}
		set
		{
			this["RadarReturnDataMode"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("0")]
	public short M
	{
		get
		{
			return (short)this["M"];
		}
		set
		{
			this["M"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("11")]
	public short N
	{
		get
		{
			return (short)this["N"];
		}
		set
		{
			this["N"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("1685")]
	public int TXS1
	{
		get
		{
			return (int)this["TXS1"];
		}
		set
		{
			this["TXS1"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("195")]
	public int TXL1
	{
		get
		{
			return (int)this["TXL1"];
		}
		set
		{
			this["TXL1"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("40685")]
	public int RXS2
	{
		get
		{
			return (int)this["RXS2"];
		}
		set
		{
			this["RXS2"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("195")]
	public int RXL2
	{
		get
		{
			return (int)this["RXL2"];
		}
		set
		{
			this["RXL2"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29")]
	public string G
	{
		get
		{
			return (string)this["G"];
		}
		set
		{
			this["G"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("8098")]
	public int LocalRecvPort
	{
		get
		{
			return (int)this["LocalRecvPort"];
		}
		set
		{
			this["LocalRecvPort"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("600")]
	public int VideoRxPeakThreshold
	{
		get
		{
			return (int)this["VideoRxPeakThreshold"];
		}
		set
		{
			this["VideoRxPeakThreshold"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("3")]
	public int VideoActiveRxHangoverFrames
	{
		get
		{
			return (int)this["VideoActiveRxHangoverFrames"];
		}
		set
		{
			this["VideoActiveRxHangoverFrames"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("32")]
	public int VideoDecodeBatchFrames
	{
		get
		{
			return (int)this["VideoDecodeBatchFrames"];
		}
		set
		{
			this["VideoDecodeBatchFrames"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("4096")]
	public int VideoDecodeQueueLimit
	{
		get
		{
			return (int)this["VideoDecodeQueueLimit"];
		}
		set
		{
			this["VideoDecodeQueueLimit"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("3")]
	public int VideoRepairRoundCount
	{
		get
		{
			return (int)this["VideoRepairRoundCount"];
		}
		set
		{
			this["VideoRepairRoundCount"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("100")]
	public int VideoRepairWaitMs
	{
		get
		{
			return (int)this["VideoRepairWaitMs"];
		}
		set
		{
			this["VideoRepairWaitMs"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("256")]
	public int VideoWirelessChunkPayloadBytes
	{
		get
		{
			return (int)this["VideoWirelessChunkPayloadBytes"];
		}
		set
		{
			this["VideoWirelessChunkPayloadBytes"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("65")]
	public int QpskTxScalePercent
	{
		get
		{
			return (int)this["QpskTxScalePercent"];
		}
		set
		{
			this["QpskTxScalePercent"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("65")]
	public int QpskPreambleThresholdPercent
	{
		get
		{
			return (int)this["QpskPreambleThresholdPercent"];
		}
		set
		{
			this["QpskPreambleThresholdPercent"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("53")]
	public int QpskQuickThresholdPercent
	{
		get
		{
			return (int)this["QpskQuickThresholdPercent"];
		}
		set
		{
			this["QpskQuickThresholdPercent"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("4")]
	public int QpskPreambleSearchStep
	{
		get
		{
			return (int)this["QpskPreambleSearchStep"];
		}
		set
		{
			this["QpskPreambleSearchStep"] = value;
		}
	}

	[UserScopedSetting]
	[DebuggerNonUserCode]
	[DefaultSettingValue("1.1")]
	public string Version
	{
		get
		{
			return (string)this["Version"];
		}
		set
		{
			this["Version"] = value;
		}
	}
}
