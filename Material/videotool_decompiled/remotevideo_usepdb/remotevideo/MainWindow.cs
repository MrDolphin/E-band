using System;
using System.CodeDom.Compiler;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Numerics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Catel.Logging;
using Choi.ByteBuffer;
using OpenCvSharp;
using OpenCvSharp.WpfExtensions;
using remotevideo.Properties;
using remotevideo.Views;

namespace remotevideo;

public class MainWindow : System.Windows.Window, IComponentConnector
{
	private static readonly ILog Log = LogManager.GetCurrentClassLogger();

	private sealed class VideoRecoveryFrameStats
	{
		public uint FrameId { get; init; }
		public int TotalChunks { get; set; }
		public int BestReceivedChunks { get; set; }
		public long FirstSeenMs { get; init; }
		public long FirstNearCompleteMs { get; set; } = -1;
		public long CompletedMs { get; set; } = -1;
	}

	private VideoCapture capture;

	private Thread m_senderThread;

	private Thread receiverThread;

	private Thread m_encodeThread;

	private Thread m_videoDecodeThread;

	private volatile bool m_isSending;

	private volatile bool m_videoPlaybackCompleted;

	private bool m_isReceiving;

	private volatile bool m_acceptRxIq;

	private UdpClient m_udpClient;

	private IPEndPoint m_sendEndPoint;

	private IPEndPoint m_recvEndPoint;

	private int m_frameDelay = 33;

	private const int MaxPacketSize = 1000;

	private const int WaveformSamplesPerFrame = 6144;

	private const int WaveformChunkPayloadBytes = 1000;

	private readonly ConcurrentQueue<Mat> m_encodeQueue = new ConcurrentQueue<Mat>();

	private readonly AutoResetEvent m_encodeEvent = new AutoResetEvent(initialState: false);

	private readonly ConcurrentQueue<(byte[] Iq, int FrameId)> m_videoDecodeQueue =
		new ConcurrentQueue<(byte[] Iq, int FrameId)>();

	private readonly AutoResetEvent m_videoDecodeEvent = new AutoResetEvent(initialState: false);

	private byte[] m_recvBuffer = Array.Empty<byte>();

	private int m_expectedPackets = -1;

	private Dictionary<int, byte[]> m_packetBuffer = new Dictionary<int, byte[]>();

	private string m_filepath;

	private string m_remoteIp;

	private int m_remotePort;

	private bool m_sendRadarCmd;

	private bool m_needUpdateParam;

	private byte m_protocolVersion;

	private bool m_deviceStarted;

	private byte m_mode;

	private byte m_commMode;

	private byte m_radarSignal;

	private byte m_radarBandWidth;

	private short m_radarTimeWidth;

	private byte _radarAzimuthLooks;

	private short m_Nd;

	private short m_Nr;

	private short m_Fc;

	private byte m_radarReturnMode;

	private short m_M;

	private short m_N;

	private int m_TxS1;

	private int m_TxL1;

	private int m_RxS2;

	private int m_RxL2;

	private List<int> m_G;

	private byte[] m_bctlvalues;

	private int m_waveformFrameId;

	private int m_rxIqFrameId = -1;

	private int m_rxIqExpectedChunks;

	private readonly Dictionary<int, byte[]> m_rxIqChunks = new Dictionary<int, byte[]>();

	private long m_rxIqPacketCount;

	private long m_rxIqCompleteFrameCount;

	private long m_rxIqDroppedFrameCount;

	private long m_rxIqSampleCount;

	private long m_videoDecodeQueueDropCount;

	private static int VideoDecodeQueueLimit => ClampSetting(
		Settings.Default.VideoDecodeQueueLimit,
		256,
		32768);

	private static int VideoDecodeBatchFrames => ClampSetting(
		Settings.Default.VideoDecodeBatchFrames,
		1,
		256);

	private static int VideoActiveRxPeakThreshold => ClampSetting(
		Settings.Default.VideoRxPeakThreshold,
		1,
		32767);

	private static int VideoActiveRxHangoverFrames => ClampSetting(
		Settings.Default.VideoActiveRxHangoverFrames,
		0,
		64);

	private byte[] m_videoDecodePrerollIq;

	private int m_videoDecodePrerollFrameId = -1;

	private int m_videoActiveRxHangover;

	private int m_lastQueuedVideoRxFrameId = -1;

	private long m_videoGateQueuedFrameCount;

	private long m_videoGateSkippedFrameCount;

	private int m_lastDecodedRxFrameId = -1;

	private double m_videoRxRms;

	private double m_videoRxPeak;

	private double m_videoRxMeanI;

	private double m_videoRxMeanQ;

	private double m_videoRxRmsI;

	private double m_videoRxRmsQ;

	private double m_videoRxIqImbalanceDb;

	private double m_videoRxIqCorrelation;

	private double m_videoRxCoarseFrequencyHz;

	private double m_videoRxClippingPercent;

	private double m_videoRxZeroPercent;

	private readonly object m_videoDiagnosticsLock = new object();

	private StreamWriter m_videoMetricsWriter;

	private FileStream m_videoIqCaptureStream;

	private StreamWriter m_videoTxManifestWriter;
	private StreamWriter m_videoRecoveryWriter;
	private long m_videoMetricsWriteCount;
	private long m_videoTxManifestWriteCount;
	private long m_videoRecoveryWriteCount;
	private const int VideoDiagnosticsFlushInterval = 256;
	private MemoryStream m_videoTxBatchStream;
	private int m_videoTxBatchChunkCount;

	private string m_videoMetricsPath = "";

	private string m_videoIqCapturePath = "";

	private string m_videoTxManifestPath = "";

	private string m_videoRecoveryPath = "";

	private long m_videoIqCaptureBytes;

	private static long MaximumVideoIqCaptureBytes =>
		(long)ClampSetting(Settings.Default.VideoIqCaptureLimitMb, 16, 2048) *
		1024L *
		1024L;

	private const double RxAdcFullScale = 2048.0;

	private long m_lastVideoDiagnosticsTick;

	private readonly WirelessVideoReassembler m_videoReassembler = new WirelessVideoReassembler();

	private readonly QpskStreamDecoder m_qpskStreamDecoder = new QpskStreamDecoder();
	private readonly object m_videoRxStatsLock = new object();
	private const byte PACING_CONTROL_PACKET_TYPE = 0x06;
	private ushort m_lastSentPacingUs = 50;
	private long m_lastPacingChangeTick = 0;

	private readonly List<byte[]> m_fmcwCpiChirps = new List<byte[]>();

	private int m_fmcwCpiIndex;

	private string m_lastFmcwCpiText = "";

	private uint m_videoModemFrameId;

	private long m_videoModemChunkCount;

	private long m_videoModemFailureCount;

	private string m_videoLastCandidateDiagnostic = "候选包诊断：尚无候选包";

	private string m_videoLastDemodDiagnostic = "解调质量：尚无有效候选包";

	private string m_videoLastParseFailureReason = "格式失败原因：尚无";

	private long m_videoWirelessCrcFailureCount;

	private long m_videoWirelessFormatFailureCount;

	private long m_videoModemDecodedChunkCount;

	private long m_videoModemRecoveredFrameCount;

	private long m_videoStaleDisplayFrameCount;

	private readonly VideoFrameDisplayOrder m_videoFrameDisplayOrder = new();

	private long m_videoRecoveryStartTick;

	private long m_videoFirstRecoveredFrameMs = -1;

	private uint m_videoLastRecoveredFrameId;

	private readonly Dictionary<uint, VideoRecoveryFrameStats> m_videoRecoveryStats =
		new Dictionary<uint, VideoRecoveryFrameStats>();

	private const int VideoChunkRepeatCount = 2;
	private const int VideoRecentRepairFrameWindow = 16;
	private const int VideoRepairRetentionFrameWindow = 32;
	private const int VideoFinalRepairRounds = 2;
	private const int VideoRepairChunkBudgetPerRound = 160;
	private const int VideoChunkPacingInterval = 8;
	private const int VideoTxBatchChunkCount = 8;
	private const int VideoRealtimeNearCompleteRepairRounds = 3;
	private const int VideoFinalNearCompleteRepairRounds = 12;
	private const int VideoNearCompleteMissingLimit = 8;
	private static int VideoWirelessChunkPayloadBytes => ClampSetting(
		Settings.Default.VideoWirelessChunkPayloadBytes,
		32,
		WirelessVideoFrame.MaxPayloadSize);
	private static int VideoRepairRoundCount => ClampSetting(
		Settings.Default.VideoRepairRoundCount,
		0,
		Mode9TuningProfile.MaxRepairRoundCount);

	private static int VideoRepairWaitMs => ClampSetting(
		Settings.Default.VideoRepairWaitMs,
		0,
		2000);

	private static double QpskTxScale => ClampSetting(
		Settings.Default.QpskTxScalePercent,
		5,
		100) / 100.0;
	private static int VideoTxMaxLongEdge => ClampSetting(
		Settings.Default.VideoTxMaxLongEdge,
		64,
		1920);
	private static int VideoTxWebPQuality => ClampSetting(
		Settings.Default.VideoTxWebPQuality,
		5,
		100);
	private const int PhotoMaxLongEdge = 800;
	private const int PhotoWebPQuality = 40;
	private long m_videoModemRepairChunkCount;
	private long m_videoModemSentFrameCount;
	private long m_videoModemConfirmedFrameCount;

	private int m_videoLastEncodedBytes;

	private int m_videoLastEncodedWidth;

	private int m_videoLastEncodedHeight;

	private int m_videoLastEncodedChunks;

	private int m_videoIqFrameId;

	private long m_videoTxManifestSequence;

	private readonly Dictionary<uint, IReadOnlyList<WirelessVideoFrame>> m_videoRecentChunks =
		new Dictionary<uint, IReadOnlyList<WirelessVideoFrame>>();

	private readonly Dictionary<uint, long> m_videoRecentChunkTicks =
		new Dictionary<uint, long>();

	internal TextBlock tbStatus;

	internal TextBlock tbListenPort;

	internal TextBlock tbVersion;

	internal Button btnStartSending;

	internal Button btnStopSending;

	internal Button btnStartDevice;

	internal Image videoDisplay;

	internal Image recvVideoDisplay;

	internal TextBox tbRadarData;

	private ScaleTransform m_recvImageScaleTransform;

	private double m_windowScale = 1.0;

	private double m_recvImageScale = 1.0;

	private double m_windowBaseWidth;

	private double m_windowBaseHeight;

	private bool _contentLoaded;

	public MainWindow()
	{
		InitializeComponent();
		LogManager.AddDebugListener();
		LogManager.IsDebugEnabled = false;
		UpgradeLegacyCommunicationDefaults();
		LoadMode9TuningProfile();
		ApplyQpskTuningSettings();
		m_protocolVersion = Settings.Default.ProtocolVersion;
		m_mode = Settings.Default.Mode;
		m_commMode = Settings.Default.CommMode;
		m_radarSignal = Settings.Default.RadarSignal;
		m_radarBandWidth = Settings.Default.RadarBandWidth;
		m_radarTimeWidth = Settings.Default.RadarTimeWidth;
		m_Nd = Settings.Default.Nd;
		m_Nr = Settings.Default.Nr;
		m_Fc = Settings.Default.Fc;
		m_radarReturnMode = Settings.Default.RadarReturnDataMode;
		m_M = Settings.Default.M;
		m_N = Settings.Default.N;
		m_TxS1 = Settings.Default.TXS1;
		m_TxL1 = Settings.Default.TXL1;
		m_RxS2 = Settings.Default.RXS2;
		m_RxL2 = Settings.Default.RXL2;
		string gvalue = Settings.Default.G;
		if (!string.IsNullOrEmpty(gvalue))
		{
			m_G = (from s in gvalue.Split(',')
				select int.Parse(s.Trim())).ToList();
		}
		m_frameDelay = Settings.Default.SendInterval;
		ConfigureInteractiveScaling();
		InitUdpSocket();
		string version = Settings.Default.Version;
		Title = AppBuildInfo.CurrentTitle(version);
		tbVersion.Text = "版本: " + version;
	}

	private static void UpgradeLegacyCommunicationDefaults()
	{
		bool changed = false;
		if (Settings.Default.CommMode == 1)
		{
			Settings.Default.CommMode = 3;
			changed = true;
		}
		if (Settings.Default.RemoteEndAddr == "127.0.0.1")
		{
			Settings.Default.RemoteEndAddr = "192.168.1.10";
			changed = true;
		}
		if (Settings.Default.RemoteEndPort == 8099)
		{
			Settings.Default.RemoteEndPort = 8080;
			changed = true;
		}
		if (changed)
		{
			Settings.Default.Save();
		}
	}

	private static int ClampSetting(int value, int min, int max)
	{
		return Math.Max(min, Math.Min(max, value));
	}

	private static void ApplyQpskTuningSettings()
	{
		QpskModem.ConfigureTuning(
			Settings.Default.QpskPreambleThresholdPercent,
			Settings.Default.QpskQuickThresholdPercent,
			Settings.Default.QpskPreambleSearchStep);
	}

	private static void LoadMode9TuningProfile()
	{
		Mode9TuningProfile profile = Mode9TuningProfile.Load(
			Mode9TuningProfile.DefaultPath);
		Settings.Default.VideoRepairRoundCount = profile.RepairRoundCount;
		Settings.Default.VideoRepairWaitMs = profile.RepairWaitMs;
		Settings.Default.QpskTxScalePercent = profile.QpskTxScalePercent;
		Settings.Default.QpskPreambleThresholdPercent = profile.PreambleThresholdPercent;
		Settings.Default.QpskQuickThresholdPercent = profile.QuickThresholdPercent;
		Settings.Default.QpskPreambleSearchStep = profile.PreambleSearchStep;
		Settings.Default.VideoWirelessChunkPayloadBytes = profile.WirelessChunkPayloadBytes;
		Settings.Default.VideoIqCaptureLimitMb = profile.IqCaptureLimitMb;
		Settings.Default.VideoTxMaxLongEdge = profile.VideoTxMaxLongEdge;
		Settings.Default.VideoTxWebPQuality = profile.VideoTxWebPQuality;
	}

	private void InitUdpSocket()
	{
		m_udpClient = new UdpClient();
		m_udpClient.Client.ReceiveBufferSize = 8 * 1024 * 1024;
		m_udpClient.Client.SendBufferSize = 4 * 1024 * 1024;
		int localPort = Settings.Default.LocalRecvPort;
		if (localPort == 0)
		{
			Log.Warning("udp local port is not provisioned");
			return;
		}
		try
		{
			m_udpClient.Client.Bind(new IPEndPoint(IPAddress.Any, localPort));
			tbListenPort.Text = $"监听端口: {localPort}";
		}
		catch (Exception exception)
		{
			Log.Error(exception);
			tbListenPort.Text = "监听端口: --";
		}
		m_recvEndPoint = new IPEndPoint(IPAddress.Any, 0);
		m_isReceiving = true;
		receiverThread = new Thread(ReceiveVideo)
		{
			IsBackground = true
		};
		receiverThread.Start();
		m_videoDecodeThread = new Thread(VideoModemDecodeProc)
		{
			IsBackground = true
		};
		m_videoDecodeThread.Start();
		Task.Run(delegate
		{
			Thread.Sleep(150);
			SendStopCommand(silent: true);
		});
	}

	private void ConfigureInteractiveScaling()
	{
		if (Content is FrameworkElement root)
		{
			m_windowBaseWidth = double.IsNaN(Width) || Width <= 0.0 ? Math.Max(ActualWidth, 1200.0) : Width;
			m_windowBaseHeight = double.IsNaN(Height) || Height <= 0.0 ? Math.Max(ActualHeight, 800.0) : Height;
			InstallWindowZoomOverlay(root);
		}

		if (recvVideoDisplay != null)
		{
			m_recvImageScaleTransform = new ScaleTransform(1.0, 1.0);
			recvVideoDisplay.RenderTransform = m_recvImageScaleTransform;
			recvVideoDisplay.RenderTransformOrigin = new System.Windows.Point(0.5, 0.5);
			recvVideoDisplay.Stretch = Stretch.Uniform;
			RenderOptions.SetBitmapScalingMode(recvVideoDisplay, BitmapScalingMode.HighQuality);
			recvVideoDisplay.MouseWheel += OnReceiveImageMouseWheel;
			recvVideoDisplay.MouseLeftButtonDown += OnReceiveImageMouseLeftButtonDown;
			InstallReceiveImageOverlay();
			recvVideoDisplay.ToolTip = "接收图像缩放：鼠标滚轮或右下角按钮；全屏查看：双击或 FULL；窗口缩放：右上角按钮或 Ctrl+鼠标滚轮";
		}

		if (videoDisplay != null)
		{
			videoDisplay.Stretch = Stretch.Uniform;
			RenderOptions.SetBitmapScalingMode(videoDisplay, BitmapScalingMode.HighQuality);
		}

		PreviewMouseWheel += OnWindowPreviewMouseWheel;
		PreviewKeyDown += OnWindowPreviewKeyDown;
	}

	private void InstallWindowZoomOverlay(FrameworkElement root)
	{
		if (Content is not Grid overlayRoot || !ReferenceEquals(overlayRoot.Tag, "codex-window-overlay"))
		{
			object originalContent = Content;
			Content = null;
			overlayRoot = new Grid
			{
				Tag = "codex-window-overlay",
			};
			if (originalContent is UIElement originalElement)
			{
				overlayRoot.Children.Add(originalElement);
			}
			Content = overlayRoot;
		}

		StackPanel panel = new StackPanel
		{
			Orientation = Orientation.Horizontal,
			HorizontalAlignment = HorizontalAlignment.Right,
			VerticalAlignment = VerticalAlignment.Top,
			Margin = new Thickness(0, 8, 12, 0),
			Background = new SolidColorBrush(Color.FromArgb(190, 28, 30, 34)),
		};
		panel.Children.Add(CreateOverlayButton("-", "缩小窗口", delegate { SetWindowScale(m_windowScale - 0.1); }));
		panel.Children.Add(CreateOverlayButton("100%", "重置窗口大小", delegate { SetWindowScale(1.0); }));
		panel.Children.Add(CreateOverlayButton("+", "放大窗口", delegate { SetWindowScale(m_windowScale + 0.1); }));
		panel.Children.Add(CreateOverlayButton("MAX", "最大化/还原窗口", delegate
		{
			WindowState = WindowState == WindowState.Maximized ?
				WindowState.Normal :
				WindowState.Maximized;
		}, 52));
		Panel.SetZIndex(panel, 1000);
		overlayRoot.Children.Add(panel);
	}

	private void InstallReceiveImageOverlay()
	{
		if (recvVideoDisplay.Parent is Grid parentGrid &&
			ReferenceEquals(parentGrid.Tag, "codex-recv-image-overlay"))
		{
			return;
		}

		Grid wrapper = new Grid
		{
			Tag = "codex-recv-image-overlay",
			Margin = recvVideoDisplay.Margin,
			HorizontalAlignment = recvVideoDisplay.HorizontalAlignment,
			VerticalAlignment = recvVideoDisplay.VerticalAlignment,
			Width = recvVideoDisplay.Width,
			Height = recvVideoDisplay.Height,
			MinWidth = recvVideoDisplay.MinWidth,
			MinHeight = recvVideoDisplay.MinHeight,
			MaxWidth = recvVideoDisplay.MaxWidth,
			MaxHeight = recvVideoDisplay.MaxHeight,
		};
		CopyGridPosition(recvVideoDisplay, wrapper);
		ReplaceElementWithWrapper(recvVideoDisplay, wrapper);
		recvVideoDisplay.Margin = new Thickness(0);
		recvVideoDisplay.HorizontalAlignment = HorizontalAlignment.Stretch;
		recvVideoDisplay.VerticalAlignment = VerticalAlignment.Stretch;
		wrapper.Children.Add(recvVideoDisplay);

		StackPanel panel = new StackPanel
		{
			Orientation = Orientation.Horizontal,
			HorizontalAlignment = HorizontalAlignment.Right,
			VerticalAlignment = VerticalAlignment.Bottom,
			Margin = new Thickness(0, 0, 10, 10),
			Background = new SolidColorBrush(Color.FromArgb(190, 22, 24, 28)),
		};
		panel.Children.Add(CreateOverlayButton("-", "缩小接收图像", delegate { SetReceiveImageScale(m_recvImageScale - 0.15); }));
		panel.Children.Add(CreateOverlayButton("+", "放大接收图像", delegate { SetReceiveImageScale(m_recvImageScale + 0.15); }));
		panel.Children.Add(CreateOverlayButton("FULL", "全屏查看接收图像", delegate { ShowReceiveImageFullScreen(); }, 54));
		Panel.SetZIndex(panel, 1000);
		wrapper.Children.Add(panel);
	}

	private static Button CreateOverlayButton(string text, string tooltip, RoutedEventHandler click, double width = 38)
	{
		Button button = new Button
		{
			Content = text,
			ToolTip = tooltip,
			Width = width,
			Height = 30,
			Margin = new Thickness(2),
			Padding = new Thickness(4, 0, 4, 0),
			FontSize = 12,
			Foreground = Brushes.White,
			Background = new SolidColorBrush(Color.FromArgb(210, 45, 48, 54)),
			BorderBrush = new SolidColorBrush(Color.FromArgb(230, 130, 135, 145)),
		};
		button.Click += click;
		return button;
	}

	private static void CopyGridPosition(FrameworkElement source, FrameworkElement target)
	{
		Grid.SetRow(target, Grid.GetRow(source));
		Grid.SetColumn(target, Grid.GetColumn(source));
		Grid.SetRowSpan(target, Grid.GetRowSpan(source));
		Grid.SetColumnSpan(target, Grid.GetColumnSpan(source));
	}

	private static void ReplaceElementWithWrapper(FrameworkElement element, Grid wrapper)
	{
		if (element.Parent is Panel panel)
		{
			int index = panel.Children.IndexOf(element);
			if (index >= 0)
			{
				panel.Children.RemoveAt(index);
				panel.Children.Insert(index, wrapper);
			}
			return;
		}

		if (element.Parent is Decorator decorator)
		{
			decorator.Child = wrapper;
			return;
		}

		if (element.Parent is ContentControl contentControl)
		{
			contentControl.Content = wrapper;
			return;
		}

		throw new InvalidOperationException("Cannot install receive image overlay for the current WPF parent.");
	}

	private void OnWindowPreviewMouseWheel(object sender, MouseWheelEventArgs e)
	{
		if ((Keyboard.Modifiers & ModifierKeys.Control) == 0)
		{
			return;
		}
		AdjustWindowScale(e.Delta > 0 ? 0.1 : -0.1);
		e.Handled = true;
	}

	private void OnWindowPreviewKeyDown(object sender, KeyEventArgs e)
	{
		if ((Keyboard.Modifiers & ModifierKeys.Control) != 0 && e.Key == Key.D0)
		{
			SetWindowScale(1.0);
			SetReceiveImageScale(1.0);
			e.Handled = true;
		}
	}

	private void OnReceiveImageMouseWheel(object sender, MouseWheelEventArgs e)
	{
		if ((Keyboard.Modifiers & ModifierKeys.Control) != 0)
		{
			return;
		}
		SetReceiveImageScale(m_recvImageScale + (e.Delta > 0 ? 0.15 : -0.15));
		e.Handled = true;
	}

	private void OnReceiveImageMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
	{
		if (e.ClickCount == 2)
		{
			ShowReceiveImageFullScreen();
			e.Handled = true;
		}
	}

	private void AdjustWindowScale(double delta)
	{
		SetWindowScale(m_windowScale + delta);
	}

	private void SetWindowScale(double scale)
	{
		m_windowScale = Math.Clamp(scale, 0.75, 1.8);
		if (WindowState == WindowState.Maximized)
		{
			WindowState = WindowState.Normal;
		}
		if (m_windowBaseWidth <= 0.0 || m_windowBaseHeight <= 0.0)
		{
			m_windowBaseWidth = Math.Max(ActualWidth, 1200.0);
			m_windowBaseHeight = Math.Max(ActualHeight, 800.0);
		}
		Width = Math.Round(m_windowBaseWidth * m_windowScale);
		Height = Math.Round(m_windowBaseHeight * m_windowScale);
		tbStatus.Text = $"窗口缩放：{m_windowScale:P0}";
	}

	private void SetReceiveImageScale(double scale)
	{
		m_recvImageScale = Math.Clamp(scale, 0.5, 6.0);
		if (m_recvImageScaleTransform != null)
		{
			m_recvImageScaleTransform.ScaleX = m_recvImageScale;
			m_recvImageScaleTransform.ScaleY = m_recvImageScale;
		}
		tbStatus.Text = $"接收图像缩放：{m_recvImageScale:P0}";
	}

	private void ShowReceiveImageFullScreen()
	{
		if (recvVideoDisplay?.Source == null)
		{
			tbStatus.Text = "接收图像为空，无法全屏查看";
			return;
		}

		Image image = new Image
		{
			Source = recvVideoDisplay.Source,
			Stretch = Stretch.Uniform,
			RenderTransformOrigin = new System.Windows.Point(0.5, 0.5),
			RenderTransform = new ScaleTransform(1.0, 1.0),
		};
		RenderOptions.SetBitmapScalingMode(image, BitmapScalingMode.HighQuality);
		ScrollViewer viewer = new ScrollViewer
		{
			Content = image,
			Background = Brushes.Black,
			HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
			VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
		};
		System.Windows.Window window = new System.Windows.Window
		{
			Title = "接收图像全屏查看",
			Content = viewer,
			WindowStyle = WindowStyle.None,
			WindowState = WindowState.Maximized,
			Background = Brushes.Black,
			Owner = this,
		};
		double scale = 1.0;
		window.PreviewMouseWheel += delegate(object sender, MouseWheelEventArgs e)
		{
			scale = Math.Clamp(scale + (e.Delta > 0 ? 0.15 : -0.15), 0.5, 8.0);
			if (image.RenderTransform is ScaleTransform transform)
			{
				transform.ScaleX = scale;
				transform.ScaleY = scale;
			}
			e.Handled = true;
		};
		window.PreviewKeyDown += delegate(object sender, KeyEventArgs e)
		{
			if (e.Key == Key.Escape)
			{
				window.Close();
				e.Handled = true;
			}
		};
		window.Show();
	}

	protected override void OnClosed(EventArgs e)
	{
		m_isSending = false;
		m_acceptRxIq = false;
		SendStopCommand(silent: true);
		m_isReceiving = false;
		m_videoDecodeEvent.Set();
		CloseVideoDiagnostics();
		m_udpClient?.Close();
		base.OnClosed(e);
	}

	private bool CreateRemoteEndpoint()
	{
		m_remoteIp = Settings.Default.RemoteEndAddr;
		m_remotePort = Settings.Default.RemoteEndPort;
		if (m_remotePort == 0 || string.IsNullOrEmpty(m_remoteIp))
		{
			MessageBox.Show("发送参数配置错误");
			return false;
		}
		if (m_sendEndPoint != null && m_sendEndPoint.Address == IPAddress.Parse(m_remoteIp) && m_sendEndPoint.Port == m_remotePort)
		{
			return true;
		}
		m_sendEndPoint = new IPEndPoint(IPAddress.Parse(m_remoteIp), m_remotePort);
		return true;
	}

	private bool TryCreateRemoteEndpointSilently()
	{
		m_remoteIp = Settings.Default.RemoteEndAddr;
		m_remotePort = Settings.Default.RemoteEndPort;
		if (m_remotePort == 0 || string.IsNullOrEmpty(m_remoteIp))
		{
			return false;
		}
		if (!IPAddress.TryParse(m_remoteIp, out IPAddress remoteAddress))
		{
			return false;
		}
		if (m_sendEndPoint != null && m_sendEndPoint.Address.Equals(remoteAddress) && m_sendEndPoint.Port == m_remotePort)
		{
			return true;
		}
		m_sendEndPoint = new IPEndPoint(remoteAddress, m_remotePort);
		return true;
	}

	private void OnStartSending_Click(object sender, RoutedEventArgs e)
	{
		if (CreateRemoteEndpoint())
		{
			ApplyQpskTuningSettings();
			SendStopCommand();
			Thread.Sleep(60);
			m_acceptRxIq = true;
			m_commMode = Settings.Default.CommMode;
			m_filepath = IsVideoTransmissionMode(m_commMode) ? Settings.Default.VideoFilePath : string.Empty;
			bool isVideoMode = IsVideoTransmissionMode(m_commMode);
			bool hasVideoFile = isVideoMode && IsVideoFilePath(m_filepath);
			bool hasStillImageFile = isVideoMode && IsStillImageFilePath(m_filepath);
			if (isVideoMode && !hasVideoFile && !hasStillImageFile)
			{
				m_acceptRxIq = false;
				tbRadarData.Text =
					"QPSK video mode needs a valid media file.\n\n" +
					"Please select an existing .mp4, .jpg, .jpeg, .png, .bmp, or .webp file in Settings -> Communication.\n" +
					"Mode 9 will not fall back to generated waveform data.";
				tbStatus.Text = "Video mode was not started: media file is missing or unsupported.";
				btnStartSending.IsEnabled = true;
				btnStopSending.IsEnabled = false;
				m_isSending = false;
				return;
			}
			m_fmcwCpiChirps.Clear();
			m_fmcwCpiIndex = 0;
			m_lastFmcwCpiText = "";
			if (m_commMode == 9)
			{
				m_qpskStreamDecoder.Clear();
				m_videoReassembler.Clear();
				m_videoModemDecodedChunkCount = 0;
				m_videoModemRecoveredFrameCount = 0;
				m_videoStaleDisplayFrameCount = 0;
				m_videoFrameDisplayOrder.Reset();
				m_videoModemFailureCount = 0;
				m_videoWirelessCrcFailureCount = 0;
				m_videoWirelessFormatFailureCount = 0;
				m_videoFirstRecoveredFrameMs = -1;
				m_videoLastRecoveredFrameId = 0;
				m_videoRecoveryStats.Clear();
				m_videoLastDemodDiagnostic = "解调质量：尚无有效候选包";
				m_videoLastParseFailureReason = "格式失败原因：尚无";
				m_videoModemRepairChunkCount = 0;
				m_videoModemSentFrameCount = 0;
				m_videoModemConfirmedFrameCount = 0;
				m_videoLastEncodedBytes = 0;
				m_videoLastEncodedWidth = 0;
				m_videoLastEncodedHeight = 0;
				m_videoLastEncodedChunks = 0;
				m_rxIqFrameId = -1;
				m_rxIqExpectedChunks = 0;
				m_rxIqChunks.Clear();
				m_rxIqPacketCount = 0;
				m_rxIqCompleteFrameCount = 0;
				m_rxIqDroppedFrameCount = 0;
				m_rxIqSampleCount = 0;
				m_videoDecodeQueueDropCount = 0;
				m_lastDecodedRxFrameId = -1;
				m_videoDecodePrerollIq = null;
				m_videoDecodePrerollFrameId = -1;
				m_videoActiveRxHangover = 0;
				m_lastQueuedVideoRxFrameId = -1;
				m_videoGateQueuedFrameCount = 0;
				m_videoGateSkippedFrameCount = 0;
				m_videoRxRms = 0.0;
				m_videoRxPeak = 0.0;
				m_videoRxMeanI = 0.0;
				m_videoRxMeanQ = 0.0;
				m_videoRxRmsI = 0.0;
				m_videoRxRmsQ = 0.0;
				m_videoRxIqImbalanceDb = 0.0;
				m_videoRxIqCorrelation = 0.0;
				m_videoRxCoarseFrequencyHz = 0.0;
				m_videoRxClippingPercent = 0.0;
				m_videoRxZeroPercent = 0.0;
				m_videoRecentChunks.Clear();
				m_videoTxBatchStream?.SetLength(0);
				m_videoTxBatchChunkCount = 0;
				m_lastVideoDiagnosticsTick = 0;
				while (m_videoDecodeQueue.TryDequeue(out _))
				{
				}
				StartVideoDiagnostics();
				tbRadarData.Text =
					"QPSK 视频 E310 链路\n\n" +
					"正在等待 E310 返回 RX IQ 数据...\n\n" +
					"如果这里一直不变化，请检查：\n" +
					"1. E310 video_modem_bridge 是否正在运行\n" +
					"2. 上位机监听端口是否为 8098\n" +
					"3. Windows 防火墙是否允许 UDP 8098";
			}
			btnStartSending.IsEnabled = false;
			btnStopSending.IsEnabled = true;
			m_isSending = true;
			m_videoPlaybackCompleted = false;
			tbStatus.Text = $"正在启动: {WaveformGenerator.GetModeName(m_commMode)} -> {m_sendEndPoint}";
			if (hasVideoFile)
			{
				m_encodeThread = new Thread(EncodeAndSendProc)
				{
					IsBackground = true
				};
				m_encodeThread.Start();
				m_senderThread = new Thread(SendVideoProc)
				{
					IsBackground = true
				};
			}
			else if (hasStillImageFile)
			{
				m_senderThread = new Thread(SendPhotoProc)
				{
					IsBackground = true
				};
			}
			else
			{
				m_senderThread = new Thread(SendGeneratedWaveformProc)
				{
					IsBackground = true
				};
			}
			m_senderThread.Start();
		}
	}

	private static bool IsVideoTransmissionMode(byte commMode)
	{
		return commMode == 8 || commMode == 9;
	}

	private static bool IsVideoFilePath(string path)
	{
		return !string.IsNullOrEmpty(path) &&
			File.Exists(path) &&
			Path.GetExtension(path).Equals(".mp4", StringComparison.OrdinalIgnoreCase);
	}

	private static bool IsStillImageFilePath(string path)
	{
		if (string.IsNullOrEmpty(path) || !File.Exists(path))
		{
			return false;
		}

		string extension = Path.GetExtension(path);
		return extension.Equals(".jpg", StringComparison.OrdinalIgnoreCase) ||
			extension.Equals(".jpeg", StringComparison.OrdinalIgnoreCase) ||
			extension.Equals(".png", StringComparison.OrdinalIgnoreCase) ||
			extension.Equals(".bmp", StringComparison.OrdinalIgnoreCase) ||
			extension.Equals(".webp", StringComparison.OrdinalIgnoreCase);
	}

	private void OnClickSendRadarData(object sender, RoutedEventArgs e)
	{
		if (CreateRemoteEndpoint())
		{
			InMemoryByteBuffer inMemoryByteBuffer = new InMemoryByteBuffer();
			inMemoryByteBuffer.Put(1, escapeIfExist: true);
			inMemoryByteBuffer.Put(220, escapeIfExist: true);
			inMemoryByteBuffer.Put(239, escapeIfExist: true);
			inMemoryByteBuffer.Put(24, escapeIfExist: true);
			inMemoryByteBuffer.Put(2, escapeIfExist: true);
			inMemoryByteBuffer.Put(m_protocolVersion);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put(0, escapeIfExist: true);
			inMemoryByteBuffer.Put((short)12);
			inMemoryByteBuffer.Put((short)17);
			inMemoryByteBuffer.Put((short)19);
			inMemoryByteBuffer.Put((short)21);
			inMemoryByteBuffer.Put((short)23);
			inMemoryByteBuffer.Put((short)25);
			inMemoryByteBuffer.Put((short)27);
			byte[] packetData = inMemoryByteBuffer.ToArray();
			m_udpClient.Send(packetData, packetData.Length, m_sendEndPoint);
		}
	}

	private bool SendStopCommand(bool silent = false)
	{
		try
		{
			bool endpointReady = silent ? TryCreateRemoteEndpointSilently() : CreateRemoteEndpoint();
			if (!endpointReady || m_udpClient == null)
			{
				return false;
			}
			byte[] stopPacket = BuildCommonPacket(4, Array.Empty<byte>());
			for (int attempt = 0; attempt < 3; attempt++)
			{
				m_udpClient.Send(stopPacket, stopPacket.Length, m_sendEndPoint);
				if (attempt < 2)
				{
					Thread.Sleep(20);
				}
			}
			Log.Info("Stop command sent three times to {0}", m_sendEndPoint);
			return true;
		}
		catch (Exception ex)
		{
			Log.Warning("Send stop command error: {0}", ex.Message);
			return false;
		}
	}

	private void SendPacingCommand(ushort pacingUs)
	{
		try
		{
			if (m_udpClient == null || m_sendEndPoint == null)
			{
				return;
			}
			byte[] content = BitConverter.GetBytes(pacingUs);
			byte[] pacingPacket = BuildCommonPacket(PACING_CONTROL_PACKET_TYPE, content);
			m_udpClient.Send(pacingPacket, pacingPacket.Length, m_sendEndPoint);
			Log.Info("Dynamic backpressure pacing command sent: {0} us", pacingUs);
		}
		catch (Exception ex)
		{
			Log.Warning("Send pacing command error: {0}", ex.Message);
		}
	}

	private void OnStopStreaming_Click(object sender, RoutedEventArgs e)
	{
		bool wasSending = m_isSending;
		m_isSending = false;
		m_acceptRxIq = false;
		bool stopSent = SendStopCommand();
		m_encodeEvent.Set();
		m_videoDecodeEvent.Set();

		if (wasSending)
		{
			if (m_senderThread != null && m_senderThread.IsAlive)
			{
				m_senderThread.Join(TimeSpan.FromSeconds(3.0));
			}
			if (m_encodeThread != null && m_encodeThread.IsAlive)
			{
				m_encodeThread.Join(TimeSpan.FromSeconds(3.0));
			}
			Mat mat;
			while (m_encodeQueue.TryDequeue(out mat))
			{
				try
				{
					mat.Dispose();
				}
				catch
				{
				}
			}
		}
		videoDisplay.Source = null;
		recvVideoDisplay.Source = null;
		m_rxIqFrameId = -1;
		m_rxIqExpectedChunks = 0;
		m_rxIqChunks.Clear();
		while (m_videoDecodeQueue.TryDequeue(out _))
		{
		}
		btnStartSending.IsEnabled = true;
		btnStopSending.IsEnabled = false;
		tbStatus.Text = stopSent
			? $"停止命令已发送 -> {m_sendEndPoint}"
			: "停止命令发送失败，请检查目标地址和端口";
	}

	private byte[] CompressWithGZip(byte[] data)
	{
		using MemoryStream compressedStream = new MemoryStream();
		using (GZipStream gzipStream = new GZipStream(compressedStream, CompressionLevel.Optimal))
		{
			gzipStream.Write(data, 0, data.Length);
		}
		return compressedStream.ToArray();
	}

	private void SendVideoProc()
	{
		capture = new VideoCapture(m_filepath);
		if (!capture.IsOpened())
		{
			m_videoPlaybackCompleted = true;
			m_encodeEvent.Set();
			return;
		}
		double fps = capture.Get(VideoCaptureProperties.Fps);
		if (double.IsNaN(fps) || fps <= 0.0)
		{
			fps = 30.0;
		}
		int delayMs = (int)Math.Round(1000.0 / fps);
		Mat frame = new Mat();
		while (m_isSending)
		{
			if (!capture.Read(frame) || frame.Empty())
			{
				m_videoPlaybackCompleted = true;
				m_encodeEvent.Set();
				break;
			}
			Mat toEncode = frame.Clone();
			if (m_encodeQueue.Count > 2 && m_encodeQueue.TryDequeue(out Mat old))
			{
				try
				{
					old.Dispose();
				}
				catch
				{
				}
			}
			m_encodeQueue.Enqueue(toEncode);
			m_encodeEvent.Set();
			BitmapSource bitmap = frame.ToBitmapSource();
			((Freezable)bitmap).Freeze();
			((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
			{
				videoDisplay.Source = bitmap;
			});
			if (delayMs > 0)
			{
				Thread.Sleep(delayMs);
			}
		}
		capture.Release();
		frame.Dispose();
	}

	private void SendPhotoProc()
	{
		bool frameConfirmed = false;
		bool stopSent = false;
		string statusText = "";
		Mat photo = null;
		Mat resizedPhoto = null;
		try
		{
			photo = Cv2.ImRead(m_filepath, ImreadModes.Color);
			if (photo.Empty())
			{
				statusText = "照片读取失败，请检查文件路径或格式。";
				return;
			}

			resizedPhoto = m_commMode == 9
				? ResizeMatForLongEdge(photo, VideoTxMaxLongEdge)
				: ResizePhotoForLink(photo);
			DisplayLocalMat(resizedPhoto);
			byte[] imageBytes = EncodeWebP(
				resizedPhoto,
				m_commMode == 9 ? VideoTxWebPQuality : PhotoWebPQuality);
			if (m_commMode == 9)
			{
				m_videoLastEncodedBytes = imageBytes.Length;
				m_videoLastEncodedWidth = resizedPhoto.Width;
				m_videoLastEncodedHeight = resizedPhoto.Height;
			}

			if (m_commMode == 8)
			{
				ProcessVideoModemSoftwareLoopback(imageBytes);
				frameConfirmed = true;
			}
			else if (m_commMode == 9)
			{
				frameConfirmed = SendVideoModemHardwareFrame(imageBytes, "照片");
			}
			else
			{
				SendVideoFrame(resizedPhoto);
				frameConfirmed = true;
			}

			statusText =
				$"照片发送完成: WebP={imageBytes.Length}字节, " +
				$"尺寸={resizedPhoto.Width}x{resizedPhoto.Height}, " +
				(frameConfirmed ? "已确认完整恢复" : "未确认完整恢复");
		}
		catch (Exception ex)
		{
			statusText = $"照片发送失败: {ex.Message}";
			Log.Warning("Send photo failed: {0}", ex.Message);
		}
		finally
		{
			m_isSending = false;
			m_acceptRxIq = false;
			stopSent = SendStopCommand();
			photo?.Dispose();
			resizedPhoto?.Dispose();
			((DispatcherObject)this).Dispatcher.BeginInvoke((Action)delegate
			{
				btnStartSending.IsEnabled = true;
				btnStopSending.IsEnabled = false;
				tbStatus.Text = string.IsNullOrEmpty(statusText)
					? (stopSent ? "照片发送结束，E310 已停止发送" : "照片发送结束，但停止命令发送失败")
					: $"{statusText}; {(stopSent ? "E310 已停止发送" : "停止命令发送失败")}";
			});
		}
	}

	private static Mat ResizePhotoForLink(Mat source)
	{
		return ResizeMatForLongEdge(source, PhotoMaxLongEdge);
	}

	private static Mat ResizeMatForLongEdge(Mat source, int maxLongEdge)
	{
		int targetLongEdge = ClampSetting(maxLongEdge, 64, 4096);
		int longEdge = Math.Max(source.Width, source.Height);
		if (longEdge <= targetLongEdge)
		{
			return source.Clone();
		}

		double scale = (double)targetLongEdge / longEdge;
		OpenCvSharp.Size targetSize = new OpenCvSharp.Size(
			Math.Max(1, (int)Math.Round(source.Width * scale)),
			Math.Max(1, (int)Math.Round(source.Height * scale)));
		Mat resized = new Mat();
		Cv2.Resize(source, resized, targetSize, 0.0, 0.0, InterpolationFlags.Area);
		return resized;
	}

	private static byte[] EncodeWebP(Mat mat, int quality)
	{
		Cv2.ImEncode(
			".webp",
			mat,
			out byte[] imageBytes,
			new ImageEncodingParam(
				ImwriteFlags.WebPQuality,
				ClampSetting(quality, 5, 100)));
		return imageBytes;
	}

	private static byte[] EncodeRealtimeWebP(Mat source, out Mat encodedMat)
	{
		int maxBytes = VideoRealtimePolicy.MaxEncodedBytes(VideoWirelessChunkPayloadBytes);
		int longEdge = VideoTxMaxLongEdge;
		int quality = VideoTxWebPQuality;
		while (true)
		{
			Mat candidate = ResizeMatForLongEdge(source, longEdge);
			byte[] imageBytes = EncodeWebP(candidate, quality);
			if (imageBytes.Length <= maxBytes || (longEdge <= 64 && quality <= 5))
			{
				encodedMat = candidate;
				return imageBytes;
			}

			candidate.Dispose();
			if (quality > 5)
			{
				quality = Math.Max(5, quality - 5);
			}
			else
			{
				longEdge = Math.Max(64, longEdge * 3 / 4);
				quality = VideoTxWebPQuality;
			}
		}
	}

	private void DisplayLocalMat(Mat mat)
	{
		BitmapSource bitmap = mat.ToBitmapSource();
		((Freezable)bitmap).Freeze();
		((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
		{
			videoDisplay.Source = bitmap;
		});
	}

	private void EncodeAndSendProc()
	{
		while (m_isSending && (!m_videoPlaybackCompleted || !m_encodeQueue.IsEmpty))
		{
			if (m_sendRadarCmd)
			{
				m_sendRadarCmd = false;
				sendRadarCommand(m_bctlvalues);
				continue;
			}
			if (!m_encodeQueue.TryDequeue(out Mat mat))
			{
				if (m_videoPlaybackCompleted)
				{
					break;
				}
				m_encodeEvent.WaitOne(100);
				continue;
			}
			while (m_encodeQueue.TryDequeue(out Mat newerMat))
			{
				mat.Dispose();
				mat = newerMat;
			}
			long sendStartedMs = Environment.TickCount64;
			try
			{
				SendVideoFrame(mat);
			}
			catch (Exception exception)
			{
				Log.Warning(exception);
			}
			finally
			{
				try
				{
					mat.Dispose();
				}
				catch
				{
				}
			}
			if (m_commMode == 9 && m_isSending)
			{
				int remainingMs = VideoRealtimePolicy.TargetFrameIntervalMs -
					(int)(Environment.TickCount64 - sendStartedMs);
				if (remainingMs > 0)
				{
					Thread.Sleep(remainingMs);
				}
			}
		}

		if (m_videoPlaybackCompleted && m_isSending)
		{
			DrainVideoRepairBacklog();
			Thread.Sleep(750);
			m_isSending = false;
			m_acceptRxIq = false;
			bool stopSent = SendStopCommand();
			((DispatcherObject)this).Dispatcher.BeginInvoke((Action)delegate
			{
				btnStartSending.IsEnabled = true;
				btnStopSending.IsEnabled = false;
				tbStatus.Text = stopSent
					? "视频已播放并发送一次，E310 已停止发送"
					: "视频已播放一次，但停止命令发送失败";
			});
		}
	}

	private void DrainVideoRepairBacklog()
	{
		if (m_commMode != 9 || m_videoRecentChunks.Count == 0)
		{
			return;
		}

		uint newestFrameId = m_videoRecentChunks.Keys.Max();
		long deadlineMs = Environment.TickCount64 + VideoRealtimePolicy.MaxFrameAgeMs;
		int repairBudget = VideoRealtimePolicy.GetRepairChunkBudget(
			VideoRealtimePolicy.MaxChunksPerFrame);
		for (int round = 0;
			round < VideoFinalRepairRounds &&
			m_isSending &&
			Environment.TickCount64 < deadlineMs;
			round++)
		{
			RepairRecentVideoFrames(
				newestFrameId,
				VideoChunkRepeatCount + round,
				repairBudget);
			FlushVideoWirelessBatch();
			Thread.Sleep(Math.Min(50, Math.Max(0, VideoRepairWaitMs)));
		}
	}

	private void sendRadarCommand(byte[] bctlvalues)
	{
		if (bctlvalues != null)
		{
			InMemoryByteBuffer buffer = new InMemoryByteBuffer();
			buffer.Put(1, escapeIfExist: true);
			buffer.Put(220, escapeIfExist: true);
			buffer.Put(239, escapeIfExist: true);
			buffer.Put(24, escapeIfExist: true);
			buffer.Put(2, escapeIfExist: true);
			buffer.Put(m_protocolVersion);
			buffer.Put(m_deviceStarted ? ((byte)1) : ((byte)0));
			buffer.Put(m_needUpdateParam ? ((byte)1) : ((byte)0));
			if (m_needUpdateParam)
			{
				m_needUpdateParam = false;
			}
			buffer.Put(m_mode);
			buffer.Put(m_commMode);
			buffer.Put(m_radarSignal);
			buffer.Put(m_radarBandWidth);
			buffer.Put(m_radarTimeWidth);
			buffer.Put(m_Nd);
			buffer.Put(m_Nr);
			buffer.Put(m_Fc);
			buffer.Put(m_radarReturnMode);
			buffer.Put(m_M);
			buffer.Put(m_N);
			buffer.Put((short)bctlvalues.Length);
			int mod = (buffer.Size + bctlvalues.Length + 2) % 4;
			int padSize = 4 - mod;
			buffer.Put((byte)padSize);
			buffer.PutBytes(bctlvalues);
			byte[] padData = new byte[padSize];
			buffer.PutBytes(padData);
			byte[] packetData = buffer.ToArray();
			try
			{
				Log.Info("packet padding: {0}, {1}", packetData.Length % 4, padSize);
				m_udpClient?.Send(packetData, packetData.Length, m_sendEndPoint);
			}
			catch (Exception ex)
			{
				Log.Warning("Send Radar Command Error: {0}", ex.Message);
			}
			buffer.Dispose();
		}
	}

	private void SendVideoFrame(Mat? mat)
	{
		if (mat == null)
		{
			return;
		}
		using Mat encodedMat = m_commMode == 9
			? null
			: mat.Clone();
		Mat realtimeEncodedMat = null;
		byte[] imageBytes = m_commMode == 9
			? EncodeRealtimeWebP(mat, out realtimeEncodedMat)
			: EncodeWebP(encodedMat, 30);
		using Mat mode9EncodedMat = realtimeEncodedMat;
		Mat transmittedMat = m_commMode == 9 ? mode9EncodedMat : encodedMat;
		if (m_commMode == 9)
		{
			m_videoLastEncodedBytes = imageBytes.Length;
			m_videoLastEncodedWidth = transmittedMat.Width;
			m_videoLastEncodedHeight = transmittedMat.Height;
		}
		if (m_commMode == 8)
		{
			ProcessVideoModemSoftwareLoopback(imageBytes);
			return;
		}
		if (m_commMode == 9)
		{
			SendVideoModemHardwareFrame(imageBytes, "视频", realtime: true);
			return;
		}
		int totalSize = imageBytes.Length;
		int numOfPackets = (int)Math.Ceiling((double)totalSize / 1000.0);
		int left = 0;
		for (int i = 0; i < numOfPackets && (m_isSending || i == numOfPackets - 1); i++)
		{
			int packetSize = Math.Min(1000, totalSize - left);
			InMemoryByteBuffer buffer = new InMemoryByteBuffer();
			buffer.Put(1, escapeIfExist: true);
			buffer.Put(220, escapeIfExist: true);
			buffer.Put(239, escapeIfExist: true);
			buffer.Put(24, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(m_protocolVersion);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			buffer.Put(0, escapeIfExist: true);
			int mod = (packetSize + 3) % 4;
			int padSize = ((mod != 0) ? (4 - mod) : 0);
			buffer.Put((short)(packetSize + 3 + padSize));
			buffer.Put((byte)padSize);
			buffer.Put((byte)numOfPackets);
			buffer.Put((byte)i);
			byte[] image = new byte[packetSize];
			Array.Copy(imageBytes, left, image, 0, packetSize);
			left += packetSize;
			buffer.PutBytes(image);
			if (padSize > 0)
			{
				byte[] padData = new byte[padSize];
				buffer.PutBytes(padData);
			}
			byte[] packetData = buffer.ToArray();
			try
			{
				Log.Info("packet padding: {0}, {1}", packetData.Length % 4, padSize);
				m_udpClient?.Send(packetData, packetData.Length, m_sendEndPoint);
			}
			catch (Exception ex)
			{
				Log.Warning("Send Video Frame Error: {0}", ex.Message);
			}
			buffer.Dispose();
		}
	}

	private bool SendVideoModemHardwareFrame(
		byte[] imageBytes,
		string mediaLabel,
		bool realtime = false)
	{
		uint frameId = m_videoModemFrameId++;
		m_videoModemSentFrameCount++;
		uint timestampMs = (uint)(Environment.TickCount64 & uint.MaxValue);
		IReadOnlyList<WirelessVideoFrame> chunks =
			WirelessVideoFrame.Fragment(
				imageBytes,
				frameId,
				timestampMs,
				VideoWirelessChunkPayloadBytes);
		m_videoLastEncodedChunks = chunks.Count;
		RememberVideoChunks(frameId, chunks);

		int repeatCount = realtime ? 1 : VideoChunkRepeatCount;
		for (int repeat = 0; repeat < repeatCount; repeat++)
		{
			for (int position = 0; position < chunks.Count; position++)
			{
				int chunkIndex = WirelessChunkSchedule.GetIndex(
					chunks.Count,
					repeat,
					position);
				WirelessVideoFrame chunk = chunks[chunkIndex];
				if (!m_isSending)
				{
					break;
				}
				SendVideoWirelessChunk(chunk, frameId, repeat, isRepair: false);
			}
			FlushVideoWirelessBatch();
			if (!m_isSending)
			{
				break;
			}
		}

		bool frameConfirmed = false;
		if (realtime && m_isSending)
		{
			RepairRecentVideoFrames(
				frameId,
				VideoRealtimePolicy.GetRepairVariant(repeatCount, frameId),
				VideoRealtimePolicy.GetRepairChunkBudget(chunks.Count),
				onlyOlderFrames: true);
			FlushVideoWirelessBatch();
			frameConfirmed =
				m_videoReassembler.TryGetMissingChunkIndices(
					frameId,
					out ushort[] missingChunkIndices,
					out bool completed) &&
				(completed || missingChunkIndices.Length == 0);
		}
		else
		{
			for (int repairRound = 0;
				repairRound < VideoRepairRoundCount && m_isSending;
				repairRound++)
			{
				Thread.Sleep(VideoRepairWaitMs);
				RepairRecentVideoFrames(frameId, repeatCount + repairRound);
				FlushVideoWirelessBatch();
				frameConfirmed =
					m_videoReassembler.TryGetMissingChunkIndices(
						frameId,
						out ushort[] missingChunkIndices,
						out bool completed) &&
					(completed || missingChunkIndices.Length == 0);
				if (frameConfirmed)
				{
					break;
				}
			}

			if (!frameConfirmed && m_isSending)
			{
				RepairNearCompleteVideoFrames(
					frameId,
					repeatCount + VideoRepairRoundCount,
					VideoRealtimeNearCompleteRepairRounds);
				frameConfirmed =
					m_videoReassembler.TryGetMissingChunkIndices(
						frameId,
						out ushort[] missingChunkIndices,
						out bool completed) &&
					(completed || missingChunkIndices.Length == 0);
			}

			if (!frameConfirmed && m_isSending)
			{
				Thread.Sleep(VideoRepairWaitMs);
				frameConfirmed =
					m_videoReassembler.TryGetMissingChunkIndices(
						frameId,
						out _,
						out bool completed) &&
					completed;
			}
		}
		if (frameConfirmed)
		{
			m_videoModemConfirmedFrameCount++;
		}

		string deliveryStatus = m_videoReassembler.GetStatus(frameId);
		string txStatus =
			$"E310{mediaLabel}TX: 帧{frameId}, WebP={imageBytes.Length}字节, " +
			$"无线分片={chunks.Count}, 补发={m_videoModemRepairChunkCount}, " +
			$"累计IQ帧={m_videoModemChunkCount}; {deliveryStatus}";
		((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
		{
			tbStatus.Text =
				$"E310视频TX: 帧={frameId}, WebP={imageBytes.Length}字节, " +
				$"无线分片={chunks.Count}, 补发={m_videoModemRepairChunkCount}, " +
				$"累计IQ帧={m_videoModemChunkCount}; {deliveryStatus}";
		});
		((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
		{
			tbStatus.Text = txStatus;
		});
		return frameConfirmed;
	}

	private void RememberVideoChunks(
		uint frameId,
		IReadOnlyList<WirelessVideoFrame> chunks)
	{
		m_videoRecentChunks[frameId] = chunks;
		m_videoRecentChunkTicks[frameId] = Environment.TickCount64;
		DiscardExpiredVideoRepairFrames(Environment.TickCount64);
		foreach (uint oldFrameId in m_videoRecentChunks.Keys
			.Where(id => frameId > id && frameId - id > VideoRepairRetentionFrameWindow)
			.ToArray())
		{
			m_videoRecentChunks.Remove(oldFrameId);
			m_videoRecentChunkTicks.Remove(oldFrameId);
		}
	}

	private void DiscardExpiredVideoRepairFrames(long nowMs)
	{
		foreach (uint frameId in m_videoRecentChunkTicks
			.Where(pair => VideoRealtimePolicy.IsExpired(pair.Value, nowMs))
			.Select(pair => pair.Key)
			.ToArray())
		{
			m_videoRecentChunkTicks.Remove(frameId);
			m_videoRecentChunks.Remove(frameId);
		}
	}

	private void RepairRecentVideoFrames(
		uint newestFrameId,
		int variant,
		int repairChunkBudget = VideoRepairChunkBudgetPerRound,
		bool onlyOlderFrames = false)
	{
		DiscardExpiredVideoRepairFrames(Environment.TickCount64);
		int repairedChunks = 0;
		var repairCandidates = m_videoRecentChunks.Keys
			.Where(id =>
				newestFrameId >= id &&
				(!onlyOlderFrames || id < newestFrameId) &&
				newestFrameId - id <= VideoRecentRepairFrameWindow)
			.Select(frameId =>
			{
				if (!m_videoRecentChunks.TryGetValue(
					frameId,
					out IReadOnlyList<WirelessVideoFrame> chunks) ||
					!m_videoReassembler.TryGetMissingChunkIndices(
						frameId,
						out ushort[] missingChunkIndices,
						out bool completed) ||
					completed ||
					missingChunkIndices.Length == 0)
				{
					return null;
				}
				return new
				{
					FrameId = frameId,
					Chunks = chunks,
					MissingChunkIndices = missingChunkIndices
				};
			})
			.Where(candidate => candidate != null)
			.OrderBy(candidate => candidate.MissingChunkIndices.Length)
			.ThenByDescending(candidate => candidate.FrameId)
			.ToArray();

		foreach (var candidate in repairCandidates)
		{
			if (!m_isSending)
			{
				break;
			}
			uint frameId = candidate.FrameId;
			IReadOnlyList<WirelessVideoFrame> chunks = candidate.Chunks;
			ushort[] missingChunkIndices = candidate.MissingChunkIndices;

			for (int position = 0; position < missingChunkIndices.Length; position++)
			{
				if (!m_isSending)
				{
					break;
				}
				if (repairedChunks >= repairChunkBudget)
				{
					return;
				}
				int scheduledIndex = WirelessChunkSchedule.GetIndex(
					missingChunkIndices.Length,
					variant,
					position);
				ushort missingChunkIndex = missingChunkIndices[scheduledIndex];
				if (missingChunkIndex >= chunks.Count)
				{
					continue;
				}
				SendVideoWirelessChunk(
					chunks[missingChunkIndex],
					frameId,
					variant,
					isRepair: true);
				repairedChunks++;
			}
		}
	}

	private void RepairNearCompleteVideoFrames(uint newestFrameId, int firstVariant, int rounds)
	{
		for (int round = 0; round < rounds && m_isSending; round++)
		{
			bool sentAny = false;
			foreach (uint frameId in m_videoRecentChunks.Keys
				.Where(id => newestFrameId >= id && newestFrameId - id <= VideoRecentRepairFrameWindow)
				.OrderBy(id => id)
				.ToArray())
			{
				if (!m_videoRecentChunks.TryGetValue(frameId, out IReadOnlyList<WirelessVideoFrame> chunks))
				{
					continue;
				}
				if (!m_videoReassembler.TryGetMissingChunkIndices(
					frameId,
					out ushort[] missingChunkIndices,
					out bool completed) ||
					completed ||
					missingChunkIndices.Length == 0)
				{
					continue;
				}
				int missingLimit = Math.Max(
					VideoNearCompleteMissingLimit,
					(int)Math.Ceiling(chunks.Count * 0.05));
				if (missingChunkIndices.Length > missingLimit)
				{
					continue;
				}
				foreach (ushort missingChunkIndex in missingChunkIndices)
				{
					if (!m_isSending || missingChunkIndex >= chunks.Count)
					{
						break;
					}
					SendVideoWirelessChunk(
						chunks[missingChunkIndex],
						frameId,
						firstVariant + round,
						isRepair: true);
					sentAny = true;
				}
			}
			FlushVideoWirelessBatch();
			if (!sentAny)
			{
				return;
			}
			Thread.Sleep(Math.Max(10, VideoRepairWaitMs / 2));
		}
	}

	private void AppendVideoWirelessBatch(byte[] iqBytes)
	{
		m_videoTxBatchStream ??= new MemoryStream();
		m_videoTxBatchStream.Write(iqBytes, 0, iqBytes.Length);
		m_videoTxBatchChunkCount++;
		if (m_videoTxBatchChunkCount >= VideoTxBatchChunkCount)
		{
			FlushVideoWirelessBatch();
		}
	}

	private void FlushVideoWirelessBatch()
	{
		if (m_videoTxBatchStream == null || m_videoTxBatchStream.Length == 0)
		{
			return;
		}
		SendWaveformFrame(m_videoTxBatchStream.ToArray(), m_videoIqFrameId++, 0x05);
		m_videoTxBatchStream.SetLength(0);
		m_videoTxBatchChunkCount = 0;
	}

	private void SendVideoWirelessChunk(
		WirelessVideoFrame chunk,
		uint frameId,
		int variant,
		bool isRepair)
	{
		byte[] wirelessBytes = chunk.Serialize();
		byte scramblerId = (byte)(
			(variant * 53 + chunk.ChunkIndex * 17 + frameId) & 0xFF);
		Complex[] samples = QpskModem.Modulate(wirelessBytes, scramblerId);
		short[] interleaved = QpskModem.ToInterleavedInt16(samples, QpskTxScale);
		byte[] iqBytes = new byte[interleaved.Length * sizeof(short)];
		Buffer.BlockCopy(interleaved, 0, iqBytes, 0, iqBytes.Length);
		WriteVideoTxManifest(chunk, frameId, variant, isRepair, wirelessBytes);
		AppendVideoWirelessBatch(iqBytes);
		m_videoModemChunkCount++;
		if (isRepair)
		{
			m_videoModemRepairChunkCount++;
		}

		if (m_videoModemChunkCount % VideoChunkPacingInterval == 0)
		{
			Thread.Sleep(1);
		}
		else
		{
			Thread.Yield();
		}
	}

	private void WriteVideoTxManifest(
		WirelessVideoFrame chunk,
		uint frameId,
		int variant,
		bool isRepair,
		byte[] wirelessBytes)
	{
		lock (m_videoDiagnosticsLock)
		{
			if (m_videoTxManifestWriter == null)
			{
				return;
			}
			uint crc = BitConverter.ToUInt32(
				wirelessBytes,
				wirelessBytes.Length - WirelessVideoFrame.CrcSize);
			string headHex = BitConverter.ToString(
					wirelessBytes,
					0,
					Math.Min(32, wirelessBytes.Length))
				.Replace("-", "");
			string packetHex = BitConverter.ToString(wirelessBytes)
				.Replace("-", "");
			m_videoTxManifestWriter.WriteLine(
				$"{DateTime.Now:O},{m_videoTxManifestSequence++}," +
				$"{frameId},{variant},{(isRepair ? 1 : 0)}," +
				$"{chunk.ChunkIndex},{chunk.ChunkCount}," +
				$"{chunk.Payload.Length},{wirelessBytes.Length}," +
				$"0x{crc:X8},{headHex},{packetHex}");
			m_videoTxManifestWriteCount++;
			if (m_videoTxManifestWriteCount % VideoDiagnosticsFlushInterval == 0)
			{
				m_videoTxManifestWriter.Flush();
			}
		}
	}

	private void RecordVideoRecoveryProgress(
		WirelessVideoFrame chunk,
		int rxFrameId,
		bool completed)
	{
		ushort[] missingChunkIndices = Array.Empty<ushort>();
		bool knownCompleted = completed;
		m_videoReassembler.TryGetMissingChunkIndices(
			chunk.FrameId,
			out missingChunkIndices,
			out knownCompleted);
		int totalChunks = Math.Max(1, (int)chunk.ChunkCount);
		int missingChunks = knownCompleted ? 0 : missingChunkIndices.Length;
		int receivedChunks = totalChunks - missingChunks;
		int nearCompleteLimit = Math.Max(
			VideoNearCompleteMissingLimit,
			(int)Math.Ceiling(totalChunks * 0.05));
		bool nearComplete =
			!knownCompleted &&
			missingChunks > 0 &&
			missingChunks <= nearCompleteLimit;
		long nowMs = Math.Max(0, Environment.TickCount64 - m_videoRecoveryStartTick);
		string eventName = "progress";
		string missingPreview = missingChunks == 0
			? ""
			: string.Join("|", missingChunkIndices
				.Take(16)
				.Select(index => index + 1));
		if (missingChunks > 16)
		{
			missingPreview += $"|...{missingChunks}";
		}

		lock (m_videoDiagnosticsLock)
		{
			if (!m_videoRecoveryStats.TryGetValue(
				chunk.FrameId,
				out VideoRecoveryFrameStats stats))
			{
				stats = new VideoRecoveryFrameStats
				{
					FrameId = chunk.FrameId,
					TotalChunks = totalChunks,
					FirstSeenMs = nowMs
				};
				m_videoRecoveryStats[chunk.FrameId] = stats;
			}
			stats.TotalChunks = totalChunks;
			if (receivedChunks <= stats.BestReceivedChunks &&
				!nearComplete &&
				!knownCompleted)
			{
				return;
			}
			stats.BestReceivedChunks = Math.Max(
				stats.BestReceivedChunks,
				receivedChunks);
			if (nearComplete && stats.FirstNearCompleteMs < 0)
			{
				stats.FirstNearCompleteMs = nowMs;
				eventName = "near_complete";
			}
			if (knownCompleted && stats.CompletedMs < 0)
			{
				stats.CompletedMs = nowMs;
				eventName = "complete";
				m_videoLastRecoveredFrameId = chunk.FrameId;
				if (m_videoFirstRecoveredFrameMs < 0)
				{
					m_videoFirstRecoveredFrameMs = nowMs;
				}
			}
			long latencyMs =
				stats.CompletedMs >= 0 ? stats.CompletedMs - stats.FirstSeenMs : -1;

			if (m_videoRecoveryWriter == null)
			{
				return;
			}
			m_videoRecoveryWriter.WriteLine(
				$"{DateTime.Now:O},{eventName},{chunk.FrameId},{totalChunks}," +
				$"{receivedChunks},{missingChunks},{missingPreview},{rxFrameId}," +
				$"{chunk.ChunkIndex + 1},{stats.FirstSeenMs}," +
				$"{stats.FirstNearCompleteMs},{stats.CompletedMs},{latencyMs}," +
				$"{m_videoModemRecoveredFrameCount},{m_videoModemDecodedChunkCount}," +
				$"{m_videoModemChunkCount},{m_videoModemRepairChunkCount}");
			m_videoRecoveryWriteCount++;
			if (eventName == "complete" ||
				eventName == "near_complete" ||
				m_videoRecoveryWriteCount % VideoDiagnosticsFlushInterval == 0)
			{
				m_videoRecoveryWriter.Flush();
			}
		}
	}

	private void ProcessVideoModemSoftwareLoopback(byte[] imageBytes)
	{
		uint frameId = m_videoModemFrameId++;
		uint timestampMs = (uint)(Environment.TickCount64 & uint.MaxValue);
		IReadOnlyList<WirelessVideoFrame> chunks =
			WirelessVideoFrame.Fragment(
				imageBytes,
				frameId,
				timestampMs,
				VideoWirelessChunkPayloadBytes);
		byte[] completedImage = null;
		double correlationSum = 0.0;
		int decodedChunks = 0;

		foreach (WirelessVideoFrame chunk in chunks)
		{
			byte[] wirelessBytes = chunk.Serialize();
			Complex[] txSamples = QpskModem.Modulate(wirelessBytes);
			m_videoModemChunkCount++;
			if (!QpskModem.TryDemodulate(
				txSamples,
				out byte[] decodedBytes,
				out double correlation) ||
				!WirelessVideoFrame.TryParse(decodedBytes, out WirelessVideoFrame decodedChunk))
			{
				m_videoModemFailureCount++;
				continue;
			}

			correlationSum += correlation;
			decodedChunks++;
			if (m_videoReassembler.TryAdd(decodedChunk, out byte[] image))
			{
				completedImage = image;
			}
		}

		double averageCorrelation = decodedChunks == 0 ? 0.0 : correlationSum / decodedChunks;
		((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
		{
			if (completedImage != null)
			{
				DisplayImage(recvVideoDisplay, completedImage);
			}
			tbRadarData.Text =
				$"QPSK 视频软件回环\n\n" +
				$"视频帧号：{frameId}\n" +
				$"WebP大小：{imageBytes.Length} 字节\n" +
				$"本帧分片：{chunks.Count}\n" +
				$"成功解调：{decodedChunks}/{chunks.Count}\n" +
				$"前导相关：{averageCorrelation:F4}\n" +
				$"累计分片：{m_videoModemChunkCount}\n" +
				$"累计失败：{m_videoModemFailureCount}\n" +
				$"CRC：{(completedImage != null ? "通过" : "未完成")}\n\n" +
				$"当前为PC内部软件回环，\n尚未经过E310和空间射频链路。";
		});
	}

	private void SendGeneratedWaveformProc()
	{
		while (m_isSending)
		{
			try
			{
				byte[] iqPayload = TryReadWaveformFile(m_filepath) ?? WaveformGenerator.GenerateInt16Iq(m_commMode, WaveformSamplesPerFrame, m_waveformFrameId);
				SendWaveformFrame(iqPayload, m_waveformFrameId++, 0x05);
				((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
				{
					tbStatus.Text = $"已发送波形帧: {m_waveformFrameId}, 调制: {WaveformGenerator.GetModeName(m_commMode)}, IQ字节: {iqPayload.Length}";
					if (m_commMode == 3 || m_commMode == 7 || m_commMode == 10)
					{
						tbRadarData.Text =
							"【发送波形（TX Waveform）- 上位机发往 E310 TX1 的基带 IQ】\n" +
							BuildTxWaveformText(
							m_commMode,
							m_waveformFrameId - 1,
							iqPayload.Length,
							iqPayload.Length / 4);
					}
					if (m_commMode == 3 || m_commMode == 7 || m_commMode == 10)
					{
						var txTrace = RxSignalAnalyzer.BuildChirpTrace(iqPayload);
						videoDisplay.Source = RenderFmcwTrace(
							txTrace,
							m_commMode == 7
								? "TX triangle FMCW: RF frequency and IQ magnitude"
								: "TX sawtooth FMCW: RF frequency and IQ magnitude");
					}
				});
				if ((m_commMode >= 2 && m_commMode <= 7) || (m_commMode >= 10 && m_commMode <= 13))
				{
					while (m_isSending)
					{
						Thread.Sleep(100);
					}
					break;
				}
			}
			catch (Exception ex)
			{
				Log.Warning("Send waveform frame error: {0}", ex.Message);
			}
			Thread.Sleep(Math.Max(1, Settings.Default.SendInterval));
		}
	}

	private byte[]? TryReadWaveformFile(string path)
	{
		if (string.IsNullOrEmpty(path) || !File.Exists(path) || Path.GetExtension(path).Equals(".mp4", StringComparison.OrdinalIgnoreCase))
		{
			return null;
		}
		byte[] bytes = File.ReadAllBytes(path);
		if (bytes.Length % 4 != 0)
		{
			Log.Warning("Waveform file length is not aligned to int16 IQ samples: {0}", bytes.Length);
		}
		return bytes;
	}

	private void SendWaveformFrame(byte[] iqPayload, int frameId, byte packetType = 0x00)
	{
		if (iqPayload.Length == 0)
		{
			return;
		}
		int chunkCount = (int)Math.Ceiling((double)iqPayload.Length / WaveformChunkPayloadBytes);
		for (int chunkIndex = 0; chunkIndex < chunkCount && m_isSending; chunkIndex++)
		{
			int offset = chunkIndex * WaveformChunkPayloadBytes;
			int chunkSize = Math.Min(WaveformChunkPayloadBytes, iqPayload.Length - offset);
			byte[] content = BuildWaveformContent(iqPayload, offset, chunkSize, frameId, chunkIndex, chunkCount);
			byte[] packet = BuildCommonPacket(packetType, content);
			for (int attempt = 0; attempt < 2; attempt++)
			{
				m_udpClient?.Send(packet, packet.Length, m_sendEndPoint);
				if (attempt < 1)
				{
					System.Threading.Thread.SpinWait(1500);
				}
			}
		}
	}

	private byte[] BuildWaveformContent(byte[] iqPayload, int offset, int chunkSize, int frameId, int chunkIndex, int chunkCount)
	{
		using MemoryStream stream = new MemoryStream(12 + chunkSize);
		using BinaryWriter writer = new BinaryWriter(stream);
		writer.Write(frameId);
		writer.Write((ushort)chunkIndex);
		writer.Write((ushort)chunkCount);
		writer.Write(WaveformGenerator.SampleFormatInt16Iq);
		writer.Write((ushort)(chunkSize / 4));
		writer.Write(iqPayload, offset, chunkSize);
		return stream.ToArray();
	}

	private byte[] BuildCommonPacket(byte packetType, byte[] content)
	{
		if (content.Length > ushort.MaxValue)
		{
			throw new ArgumentOutOfRangeException(nameof(content), "UDP content is too large for the protocol length field.");
		}
		using MemoryStream stream = new MemoryStream(17 + content.Length);
		using BinaryWriter writer = new BinaryWriter(stream);
		writer.Write((byte)1);
		writer.Write((byte)220);
		writer.Write((byte)239);
		writer.Write((byte)24);
		writer.Write(packetType);
		writer.Write(m_protocolVersion);
		writer.Write(new byte[9]);
		writer.Write((ushort)content.Length);
		writer.Write(content);
		return stream.ToArray();
	}

	public void DisplayImage(Image imageControl, byte[] jpegBytes)
	{
		using MemoryStream stream = new MemoryStream(jpegBytes);
		BitmapImage bitmap = new BitmapImage();
		bitmap.BeginInit();
		bitmap.CacheOption = BitmapCacheOption.OnLoad;
		bitmap.StreamSource = stream;
		bitmap.EndInit();
		imageControl.Source = bitmap;
	}

	private byte[]? ParsePacket(byte[] newData)
	{
		byte[] combined = new byte[m_recvBuffer.Length + newData.Length];
		if (m_recvBuffer.Length != 0)
		{
			Array.Copy(m_recvBuffer, 0, combined, 0, m_recvBuffer.Length);
		}
		Array.Copy(newData, 0, combined, m_recvBuffer.Length, newData.Length);
		m_recvBuffer = combined;
		int headerPos = -1;
		for (int i = 0; i + 3 < m_recvBuffer.Length; i++)
		{
			if (m_recvBuffer[i] == 1 && m_recvBuffer[i + 1] == 220 && m_recvBuffer[i + 2] == 239 && m_recvBuffer[i + 3] == 24)
			{
				headerPos = i;
				break;
			}
		}
		if (headerPos == -1)
		{
			int keep = Math.Min(3, m_recvBuffer.Length);
			if (keep > 0)
			{
				byte[] tail = new byte[keep];
				Array.Copy(m_recvBuffer, m_recvBuffer.Length - keep, tail, 0, keep);
				m_recvBuffer = tail;
			}
			else
			{
				m_recvBuffer = Array.Empty<byte>();
			}
			return null;
		}
		if (headerPos > 0)
		{
			int remainingLength = m_recvBuffer.Length - headerPos;
			byte[] tmp = new byte[remainingLength];
			Array.Copy(m_recvBuffer, headerPos, tmp, 0, remainingLength);
			m_recvBuffer = tmp;
		}
		if (m_recvBuffer.Length < 17)
		{
			Log.Warning("Insufficient data for fixed header");
			return null;
		}
		int packetSizeField = BitConverter.ToUInt16(m_recvBuffer, 15);
		int fullLen = 17 + packetSizeField;
		if (m_recvBuffer.Length < fullLen)
		{
			Log.Warning("full packet insufficient");
			return null;
		}
		byte[] packet = new byte[fullLen];
		Array.Copy(m_recvBuffer, 0, packet, 0, fullLen);
		int remLen = m_recvBuffer.Length - fullLen;
		if (remLen > 0)
		{
			byte[] tail2 = new byte[remLen];
			Array.Copy(m_recvBuffer, fullLen, tail2, 0, remLen);
			m_recvBuffer = tail2;
		}
		else
		{
			m_recvBuffer = Array.Empty<byte>();
		}
		return packet;
	}

	private void ReceiveVideo()
	{
		while (m_isReceiving)
		{
			try
			{
				byte[] raw = m_udpClient.Receive(ref m_recvEndPoint);
				byte[] packetData = ParsePacket(raw);
				if (packetData == null || packetData.Length < 16)
				{
					continue;
				}
				byte packetType = packetData[4];
				if (packetType == 1)
				{
					ParseRadarPacket(packetData);
				}
				else if (packetType == 3)
				{
					ParseRxIqPacket(packetData);
				}
				else
				{
					ParseVideoPacket(packetData);
				}
			}
			catch (Exception ex)
			{
				Log.Warning("recv video exception, " + ex.ToString());
			}
		}
		m_udpClient.Close();
	}

	private void ParseRxIqPacket(byte[] packetData)
	{
		if (!m_acceptRxIq)
		{
			return;
		}
		m_rxIqPacketCount++;
		ushort contentLength = BitConverter.ToUInt16(packetData, 15);
		if (contentLength < 12 || packetData.Length < 17 + contentLength)
		{
			return;
		}

		int frameId = BitConverter.ToInt32(packetData, 17);
		int chunkIndex = BitConverter.ToUInt16(packetData, 21);
		int chunkCount = BitConverter.ToUInt16(packetData, 23);
		ushort sampleFormat = BitConverter.ToUInt16(packetData, 25);
		int sampleCount = BitConverter.ToUInt16(packetData, 27);
		int payloadBytes = contentLength - 12;
		if (sampleFormat != WaveformGenerator.SampleFormatInt16Iq || sampleCount * 4 > payloadBytes)
		{
			return;
		}
		if (chunkCount <= 0 || chunkIndex < 0 || chunkIndex >= chunkCount)
		{
			return;
		}

		if (frameId != m_rxIqFrameId)
		{
			if (m_rxIqFrameId >= 0 &&
				m_rxIqChunks.Count > 0 &&
				m_rxIqChunks.Count != m_rxIqExpectedChunks)
			{
				m_rxIqDroppedFrameCount++;
			}
			m_rxIqFrameId = frameId;
			m_rxIqExpectedChunks = chunkCount;
			m_rxIqChunks.Clear();
		}

		byte[] payload = new byte[sampleCount * 4];
		Array.Copy(packetData, 29, payload, 0, payload.Length);
		m_rxIqChunks[chunkIndex] = payload;
		if (m_rxIqChunks.Count != m_rxIqExpectedChunks)
		{
			if (m_commMode == 9)
			{
				UpdateVideoModemWaitingStatus(
					frameId,
					m_qpskStreamDecoder.LastCorrelation);
			}
			return;
		}

		using MemoryStream stream = new MemoryStream();
		for (int i = 0; i < m_rxIqExpectedChunks; i++)
		{
			if (!m_rxIqChunks.TryGetValue(i, out byte[] chunk))
			{
				return;
			}
			stream.Write(chunk, 0, chunk.Length);
		}
		byte[] completeIq = stream.ToArray();
		m_rxIqChunks.Clear();
		m_rxIqCompleteFrameCount++;
		m_rxIqSampleCount += completeIq.Length / 4;

		if (m_commMode == 9)
		{
			CaptureVideoIq(completeIq);
			MeasureVideoIq(completeIq, frameId);
			bool active = IsLikelyVideoIqActive(completeIq);
			if (active)
			{
				if (m_videoDecodePrerollIq != null &&
					m_videoDecodePrerollFrameId != m_lastQueuedVideoRxFrameId)
				{
					QueueVideoDecodeFrame(
						m_videoDecodePrerollIq,
						m_videoDecodePrerollFrameId);
				}
				m_videoActiveRxHangover = VideoActiveRxHangoverFrames;
			}
			if (active || m_videoActiveRxHangover > 0)
			{
				QueueVideoDecodeFrame(completeIq, frameId);
				if (!active)
				{
					m_videoActiveRxHangover--;
				}
			}
			else
			{
				m_videoDecodePrerollIq = completeIq;
				m_videoDecodePrerollFrameId = frameId;
				m_videoGateSkippedFrameCount++;
			}
			return;
		}

		if (completeIq.Length / 4 != WaveformSamplesPerFrame)
		{
			return;
		}

		if (m_commMode == 10)
		{
			try
			{
				var chirp = RxSignalAnalyzer.AnalyzeChirp(completeIq);
				var trace = RxSignalAnalyzer.BuildChirpTrace(completeIq);
				((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
				{
					tbRadarData.Text =
						$"RX FMCW 分析（无混频恢复版）\n\n" +
						$"帧号：{frameId}\n" +
						$"扫频方向：{chirp.Direction}\n" +
						$"起始频率：{chirp.StartRfHz / 1e6:F3} MHz\n" +
						$"终止频率：{chirp.EndRfHz / 1e6:F3} MHz\n" +
						$"扫频带宽：{chirp.BandwidthHz / 1e6:F3} MHz\n" +
						$"有效扫频：{chirp.ActiveTimeSeconds * 1e6:F2} us\n" +
						$"空闲时间：{chirp.IdleTimeSeconds * 1e6:F2} us\n" +
						$"PRT：{chirp.PrtSeconds * 1e6:F2} us\n" +
						$"线性误差：{chirp.LinearityRmsHz / 1e3:F1} kHz\n\n" +
						$"拍频：未启用\n" +
						$"估算距离：未启用\n\n" +
						$"说明：当前版本只恢复 TX/RX FMCW 扫频曲线显示，\n" +
						$"不做 PC 端去斜混频和距离 FFT。";
					recvVideoDisplay.Source = RenderFmcwTrace(
						trace,
						"RX measured FMCW: RF frequency and IQ magnitude");
				});
			}
			catch (Exception ex)
			{
				Log.Warning("RX FMCW no-mixer analysis failed: {0}", ex.Message);
			}
			return;
		}

		if (m_commMode == 3)
		{
			try
			{
				m_fmcwCpiChirps.Add(completeIq);
				if (m_fmcwCpiChirps.Count < 64)
				{
					if (m_fmcwCpiChirps.Count == 1 || m_fmcwCpiChirps.Count % 8 == 0)
					{
						int collected = m_fmcwCpiChirps.Count;
						((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
						{
							tbRadarData.Text = BuildFmcwLiveStatusText(
								m_fmcwCpiIndex,
								collected,
								frameId,
								m_lastFmcwCpiText);
						});
					}
					return;
				}

				var result = FmcwProcessor.ProcessCpi(m_fmcwCpiChirps, ++m_fmcwCpiIndex);
				m_fmcwCpiChirps.Clear();
				((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
				{
					m_lastFmcwCpiText = BuildFmcwCpiText(result, frameId);
					tbRadarData.Text = BuildFmcwLiveStatusText(
						result.CpiIndex,
						0,
						frameId,
						m_lastFmcwCpiText);
					recvVideoDisplay.Source = RenderFmcwCpiResult(result);
				});
			}
			catch (Exception ex)
			{
				m_fmcwCpiChirps.Clear();
				Log.Warning("RX FMCW CPI processing failed: {0}", ex.Message);
			}
			return;
		}

		if (frameId % 50 != 0)
		{
			return;
		}

		if (m_commMode == 2 || (m_commMode >= 4 && m_commMode <= 6) || (m_commMode >= 11 && m_commMode <= 13))
		{
			try
			{
				var result = RxSignalAnalyzer.AnalyzeTone(completeIq);
				((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
				{
					tbRadarData.Text =
						$"RX 单音分析\n\n" +
						$"帧号：{frameId}\n" +
						$"模式：{WaveformGenerator.GetModeName(m_commMode)}\n" +
						$"射频频率：{result.RfFrequencyHz / 1e6:F6} MHz\n" +
						$"基带频率：{result.BasebandFrequencyHz / 1e6:+0.000000;-0.000000;0.000000} MHz\n" +
						$"RMS：{result.Rms:F4}\n" +
						$"峰值：{result.Peak:F4}\n" +
						$"峰均比：{result.PaprDb:F2} dB";
				});
			}
			catch (Exception ex)
			{
				Log.Warning("RX tone analysis failed: {0}", ex.Message);
			}
		}
		else if (m_commMode == 3)
		{
			try
			{
				var chirp = RxSignalAnalyzer.AnalyzeChirp(completeIq);
				var trace = RxSignalAnalyzer.BuildChirpTrace(completeIq);
				var range = FmcwProcessor.Process(completeIq);
				((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
				{
					tbRadarData.Text =
						$"RX FMCW 分析\n\n" +
						$"帧号：{frameId}\n" +
						$"扫频方向：{chirp.Direction}\n" +
						$"起始频率：{chirp.StartRfHz / 1e6:F3} MHz\n" +
						$"终止频率：{chirp.EndRfHz / 1e6:F3} MHz\n" +
						$"扫频带宽：{chirp.BandwidthHz / 1e6:F3} MHz\n" +
						$"有效扫频：{chirp.ActiveTimeSeconds * 1e6:F2} us\n" +
						$"空闲时间：{chirp.IdleTimeSeconds * 1e6:F2} us\n" +
						$"PRT：{chirp.PrtSeconds * 1e6:F2} us\n" +
						$"线性误差：{chirp.LinearityRmsHz / 1e3:F1} kHz\n\n" +
						$"拍频：{range.BeatFrequencyHz:F1} Hz\n" +
						$"估算距离：{range.DistanceMeters:F2} m\n\n" +
						$"注意：当前 RX 未进行硬件帧同步，\n距离结果仅用于回环和算法验证。";
					recvVideoDisplay.Source = RenderFmcwTrace(
						trace,
						"RX measured: RF frequency and IQ magnitude");
				});
			}
			catch (Exception ex)
			{
				Log.Warning("RX FMCW analysis failed: {0}", ex.Message);
			}
		}
	}

	private void ProcessVideoModemRxIq(byte[] iq, int rxFrameId)
	{
		QpskStreamDecoder localDecoder = new QpskStreamDecoder();
		localDecoder.AppendInt16Iq(iq);
		while (localDecoder.TryReadFrame(
			out byte[] wirelessBytes,
			out double correlation))
		{
			if (!WirelessVideoFrame.TryParse(
				wirelessBytes,
				out WirelessVideoFrame chunk))
			{
				lock (m_videoRxStatsLock)
				{
					m_videoModemFailureCount++;
					string parseReason = AnalyzeWirelessFrameParseFailure(wirelessBytes);
					m_videoLastParseFailureReason = parseReason;
					if (parseReason.StartsWith("CRC", StringComparison.OrdinalIgnoreCase))
					{
						m_videoWirelessCrcFailureCount++;
					}
					else
					{
						m_videoWirelessFormatFailureCount++;
					}
					m_videoLastDemodDiagnostic = BuildQpskDemodDiagnostic(
						QpskModem.LastDiagnostics);
					m_videoLastCandidateDiagnostic = BuildVideoCandidateDiagnostic(
						rxFrameId,
						correlation,
						accepted: false,
						null,
						localDecoder);
				}
				continue;
			}

			lock (m_videoRxStatsLock)
			{
				m_videoModemDecodedChunkCount++;
				m_videoLastParseFailureReason = "格式失败原因：最近候选包已通过";
				m_videoLastDemodDiagnostic = BuildQpskDemodDiagnostic(
					QpskModem.LastDiagnostics);
				m_videoLastCandidateDiagnostic = BuildVideoCandidateDiagnostic(
					rxFrameId,
					correlation,
					accepted: true,
					chunk,
					localDecoder);
			}

			byte[] completedImage = null;
			bool displayCompletedImage = false;
			bool completedFrame = false;
			if (m_videoReassembler.TryAdd(chunk, out byte[] image))
			{
				completedImage = image;
				lock (m_videoRxStatsLock)
				{
					m_videoModemRecoveredFrameCount++;
				}
				completedFrame = true;
				displayCompletedImage = m_videoFrameDisplayOrder.TryAdvance(chunk.FrameId);
				if (!displayCompletedImage)
				{
					lock (m_videoRxStatsLock)
					{
						m_videoStaleDisplayFrameCount++;
					}
				}
			}
			RecordVideoRecoveryProgress(chunk, rxFrameId, completedFrame);
			((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
			{
				if (completedImage != null && displayCompletedImage)
				{
					DisplayImage(recvVideoDisplay, completedImage);
				}
			});
		}

		lock (m_videoRxStatsLock)
		{
			m_qpskStreamDecoder.CopyStatsFrom(localDecoder);
			UpdateVideoModemWaitingStatus(
				rxFrameId,
				localDecoder.LastCorrelation);
		}
	}

	private string BuildVideoCandidateDiagnostic(
		int rxFrameId,
		double correlation,
		bool accepted,
		WirelessVideoFrame chunk,
		QpskStreamDecoder decoder)
	{
		int start = decoder.LastFrameStart;
		int consumed = decoder.LastConsumedSamples;
		int rxFrameSpan = consumed <= 0 ? 0 : (consumed + 4095) / 4096;
		string result = accepted ? "OK" : "CRC/FORMAT FAIL";
		string chunkText = chunk == null
			? string.Empty
			: $" chunk={chunk.ChunkIndex + 1}/{chunk.ChunkCount}";
		return
			$"候选包诊断 (Candidate): {result}{chunkText}\n" +
			$"RX frame={rxFrameId}, start={start} samples, consumed={consumed} samples\n" +
			$"span≈{rxFrameSpan} RX frames @4096 samples, corr={correlation:F4}, conj={(decoder.LastUsedConjugate ? "yes" : "no")}";
	}

	private static string AnalyzeWirelessFrameParseFailure(byte[] data)
	{
		if (data == null || data.Length < WirelessVideoFrame.HeaderSize + WirelessVideoFrame.CrcSize)
		{
			return $"FORMAT: packet too short ({data?.Length ?? 0} bytes)";
		}
		uint sync = BitConverter.ToUInt32(data, 0);
		if (sync != WirelessVideoFrame.SyncWord)
		{
			return $"FORMAT: sync mismatch 0x{sync:X8}";
		}
		if (data[4] != WirelessVideoFrame.Version)
		{
			return $"FORMAT: version mismatch {data[4]}";
		}
		ushort payloadLength = BitConverter.ToUInt16(data, 14);
		int expectedLength = WirelessVideoFrame.HeaderSize + payloadLength + WirelessVideoFrame.CrcSize;
		if (payloadLength > WirelessVideoFrame.MaxPayloadSize ||
			data.Length != expectedLength)
		{
			return $"FORMAT: payload length {payloadLength}, packet {data.Length}, expected {expectedLength}";
		}
		uint expectedCrc = BitConverter.ToUInt32(
			data,
			expectedLength - WirelessVideoFrame.CrcSize);
		uint actualCrc = Crc32.Compute(data.AsSpan(0, expectedLength - WirelessVideoFrame.CrcSize));
		if (actualCrc != expectedCrc)
		{
			return $"CRC: expected 0x{expectedCrc:X8}, actual 0x{actualCrc:X8}";
		}
		ushort chunkIndex = BitConverter.ToUInt16(data, 10);
		ushort chunkCount = BitConverter.ToUInt16(data, 12);
		if (chunkCount == 0 || chunkIndex >= chunkCount)
		{
			return $"FORMAT: chunk {chunkIndex + 1}/{chunkCount}";
		}
		return "FORMAT: unknown parse failure";
	}

	private static string BuildQpskDemodDiagnostic(QpskDemodulationDiagnostics diagnostics)
	{
		if (diagnostics == null || diagnostics.Segments.Count == 0)
		{
			return "解调质量：无 payload 诊断";
		}
		double minMargin = diagnostics.Segments.Min(segment => segment.AverageDecisionMargin);
		double maxPhaseError = diagnostics.Segments.Max(segment => segment.PhaseErrorRms);
		QpskSymbolSegment worstMargin = diagnostics.Segments
			.OrderBy(segment => segment.AverageDecisionMargin)
			.First();
		QpskSymbolSegment worstPhase = diagnostics.Segments
			.OrderByDescending(segment => segment.PhaseErrorRms)
			.First();
		string firstSegments = string.Join(
			" | ",
			diagnostics.Segments
				.Take(4)
				.Select(segment =>
					$"{segment.Index}:m={segment.AverageDecisionMargin:F2},pe={segment.PhaseErrorRms:F2}"));
		string lastSegments = string.Join(
			" | ",
			diagnostics.Segments
				.Skip(Math.Max(0, diagnostics.Segments.Count - 4))
				.Select(segment =>
					$"{segment.Index}:m={segment.AverageDecisionMargin:F2},pe={segment.PhaseErrorRms:F2}"));
		return
			$"解调质量：segments={diagnostics.Segments.Count}, " +
			$"minMargin={minMargin:F2}@{worstMargin.Index}, " +
			$"maxPhaseErr={maxPhaseError:F2}@{worstPhase.Index}\n" +
			$"前段：{firstSegments}\n" +
			$"尾段：{lastSegments}";
	}

	private void VideoModemDecodeProc()
	{
		while (m_isReceiving)
		{
			if (!m_videoDecodeQueue.TryDequeue(out var item))
			{
				m_videoDecodeEvent.WaitOne(100);
				continue;
			}
			try
			{
				if (VideoRxContinuityPolicy.ShouldResetDecoder(
					m_lastDecodedRxFrameId,
					item.FrameId))
				{
					m_qpskStreamDecoder.Clear();
				}
				using MemoryStream batch = new MemoryStream();
				batch.Write(item.Iq, 0, item.Iq.Length);
				int lastFrameId = item.FrameId;
				int batchFrames = 1;
				while (batchFrames < VideoDecodeBatchFrames &&
					m_videoDecodeQueue.TryPeek(out var next) &&
					next.FrameId == lastFrameId + 1 &&
					m_videoDecodeQueue.TryDequeue(out next))
				{
					batch.Write(next.Iq, 0, next.Iq.Length);
					lastFrameId = next.FrameId;
					batchFrames++;
				}
				byte[] batchIq = batch.ToArray();
				int frameId = lastFrameId;
				System.Threading.Tasks.Task.Run(() =>
				{
					try
					{
						ProcessVideoModemRxIq(batchIq, frameId);
					}
					catch (Exception ex)
					{
						Log.Warning("Async video modem decode failed: {0}", ex.Message);
					}
				});
				m_lastDecodedRxFrameId = lastFrameId;
			}
			catch (Exception ex)
			{
				Log.Warning("Video modem decode failed: {0}", ex.Message);
			}
		}
	}

	private void StartVideoDiagnostics()
	{
		lock (m_videoDiagnosticsLock)
		{
			CloseVideoDiagnosticsLocked();
			string directory = Path.Combine(AppContext.BaseDirectory, "diagnostics");
			Directory.CreateDirectory(directory);
			string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
			m_videoMetricsPath = Path.Combine(
				directory,
				$"rx_metrics_{timestamp}.csv");
			m_videoIqCapturePath = Path.Combine(
				directory,
				$"rx_capture_{timestamp}.iq");
			m_videoTxManifestPath = Path.Combine(
				directory,
				$"tx_wireless_{timestamp}.csv");
			m_videoRecoveryPath = Path.Combine(
				directory,
				$"video_recovery_{timestamp}.csv");
			m_videoMetricsWriter = new StreamWriter(
				m_videoMetricsPath,
				append: false);
			m_videoMetricsWriter.WriteLine(
				"time,rx_frame,samples,mean_i,mean_q,rms_i,rms_q,rms_total," +
				"peak,iq_imbalance_db,iq_correlation,coarse_frequency_hz," +
				"clipping_percent,zero_percent,normal_correlation," +
				"conjugate_correlation,decode_queue,decode_queue_drops," +
				"gate_queued_frames,gate_skipped_frames,decoder_frame_start," +
				"decoder_consumed_samples,decoder_used_conjugate,iq_capture_mb");
			m_videoMetricsWriter.Flush();
			m_videoIqCaptureStream = new FileStream(
				m_videoIqCapturePath,
				FileMode.Create,
				FileAccess.Write,
				FileShare.Read);
			m_videoTxManifestWriter = new StreamWriter(
				m_videoTxManifestPath,
				append: false);
			m_videoTxManifestWriter.WriteLine(
				"time,seq,source_frame,variant,is_repair,chunk_index," +
				"chunk_count,payload_len,wireless_len,crc32,head_hex,packet_hex");
			m_videoTxManifestWriter.Flush();
			m_videoRecoveryWriter = new StreamWriter(
				m_videoRecoveryPath,
				append: false);
			m_videoRecoveryWriter.WriteLine(
				"time,event,frame_id,total_chunks,received_chunks,missing_chunks," +
				"missing_preview,rx_frame,chunk_index,first_seen_ms,near_complete_ms," +
				"completed_ms,recovery_latency_ms,recovered_frames,decoded_chunks," +
				"tx_chunks,repair_chunks");
			m_videoRecoveryWriter.Flush();
			m_videoMetricsWriteCount = 0;
			m_videoTxManifestWriteCount = 0;
			m_videoRecoveryWriteCount = 0;
			m_videoIqCaptureBytes = 0;
			m_videoTxManifestSequence = 0;
			m_videoRecoveryStartTick = Environment.TickCount64;
		}
	}

	private void CloseVideoDiagnostics()
	{
		lock (m_videoDiagnosticsLock)
		{
			CloseVideoDiagnosticsLocked();
		}
	}

	private void CloseVideoDiagnosticsLocked()
	{
		m_videoMetricsWriter?.Flush();
		m_videoMetricsWriter?.Dispose();
		m_videoMetricsWriter = null;
		m_videoIqCaptureStream?.Dispose();
		m_videoIqCaptureStream = null;
		m_videoTxManifestWriter?.Flush();
		m_videoTxManifestWriter?.Dispose();
		m_videoTxManifestWriter = null;
		m_videoRecoveryWriter?.Flush();
		m_videoRecoveryWriter?.Dispose();
		m_videoRecoveryWriter = null;
	}

	private void MeasureVideoIq(byte[] iq, int rxFrameId)
	{
		double sumI = 0.0;
		double sumQ = 0.0;
		double powerI = 0.0;
		double powerQ = 0.0;
		double crossPower = 0.0;
		double peak = 0.0;
		long clipping = 0;
		long zeroSamples = 0;
		Complex fourthPowerStep = Complex.Zero;
		Complex previousFourthPower = Complex.Zero;
		bool havePrevious = false;
		int samples = iq.Length / 4;
		for (int index = 0; index < samples; index++)
		{
			int offset = index * 4;
			short i = (short)(iq[offset] | iq[offset + 1] << 8);
			short q = (short)(iq[offset + 2] | iq[offset + 3] << 8);
			double normalizedI = i / RxAdcFullScale;
			double normalizedQ = q / RxAdcFullScale;
			sumI += normalizedI;
			sumQ += normalizedQ;
			powerI += normalizedI * normalizedI;
			powerQ += normalizedQ * normalizedQ;
			crossPower += normalizedI * normalizedQ;
			double magnitudeSquared =
				normalizedI * normalizedI + normalizedQ * normalizedQ;
			peak = Math.Max(peak, Math.Sqrt(magnitudeSquared));
			if (Math.Abs(i) >= 2047 || Math.Abs(q) >= 2047)
			{
				clipping++;
			}
			if (i == 0 && q == 0)
			{
				zeroSamples++;
			}
			Complex value = new Complex(normalizedI, normalizedQ);
			Complex squared = value * value;
			Complex fourthPower = squared * squared;
			if (havePrevious)
			{
				fourthPowerStep +=
					fourthPower * Complex.Conjugate(previousFourthPower);
			}
			previousFourthPower = fourthPower;
			havePrevious = true;
		}
		if (samples > 0)
		{
			double meanI = sumI / samples;
			double meanQ = sumQ / samples;
			double rmsI = Math.Sqrt(powerI / samples);
			double rmsQ = Math.Sqrt(powerQ / samples);
			double totalPower = powerI + powerQ;
			m_videoRxRms = Math.Sqrt(totalPower / samples);
			m_videoRxPeak = peak;
			m_videoRxMeanI = meanI;
			m_videoRxMeanQ = meanQ;
			m_videoRxRmsI = rmsI;
			m_videoRxRmsQ = rmsQ;
			m_videoRxIqImbalanceDb = 10.0 * Math.Log10(
				Math.Max(powerI, 1e-20) / Math.Max(powerQ, 1e-20));
			m_videoRxIqCorrelation = crossPower /
				Math.Sqrt(Math.Max(powerI * powerQ, 1e-20));
			m_videoRxCoarseFrequencyHz =
				fourthPowerStep.Magnitude <= 1e-20
					? 0.0
					: fourthPowerStep.Phase /
						(8.0 * Math.PI) *
						3840000.0;
			m_videoRxClippingPercent = clipping * 100.0 / samples;
			m_videoRxZeroPercent = zeroSamples * 100.0 / samples;
			WriteVideoDiagnostics(rxFrameId, samples);
		}
	}

	private void WriteVideoDiagnostics(int rxFrameId, int samples)
	{
		lock (m_videoDiagnosticsLock)
		{
			if (m_videoMetricsWriter == null)
			{
				return;
			}
			m_videoMetricsWriter.WriteLine(
				$"{DateTime.Now:O},{rxFrameId},{samples}," +
				$"{m_videoRxMeanI:F8},{m_videoRxMeanQ:F8}," +
				$"{m_videoRxRmsI:F8},{m_videoRxRmsQ:F8}," +
				$"{m_videoRxRms:F8},{m_videoRxPeak:F8}," +
				$"{m_videoRxIqImbalanceDb:F4},{m_videoRxIqCorrelation:F6}," +
				$"{m_videoRxCoarseFrequencyHz:F3}," +
				$"{m_videoRxClippingPercent:F6},{m_videoRxZeroPercent:F6}," +
				$"{m_qpskStreamDecoder.LastNormalCorrelation:F6}," +
				$"{m_qpskStreamDecoder.LastConjugateCorrelation:F6}," +
				$"{m_videoDecodeQueue.Count},{m_videoDecodeQueueDropCount}," +
				$"{m_videoGateQueuedFrameCount},{m_videoGateSkippedFrameCount}," +
				$"{m_qpskStreamDecoder.LastFrameStart}," +
				$"{m_qpskStreamDecoder.LastConsumedSamples}," +
				$"{(m_qpskStreamDecoder.LastUsedConjugate ? 1 : 0)}," +
				$"{m_videoIqCaptureBytes / (1024.0 * 1024.0):F2}");
			m_videoMetricsWriteCount++;
			if (m_videoMetricsWriteCount % VideoDiagnosticsFlushInterval == 0)
			{
				m_videoMetricsWriter.Flush();
			}

		}
	}

	private void CaptureVideoIq(byte[] iq)
	{
		lock (m_videoDiagnosticsLock)
		{
			if (m_videoIqCaptureStream == null ||
				m_videoIqCaptureBytes >= MaximumVideoIqCaptureBytes)
			{
				return;
			}
			int bytesToWrite = (int)Math.Min(
				iq.Length,
				MaximumVideoIqCaptureBytes - m_videoIqCaptureBytes);
			m_videoIqCaptureStream.Write(iq, 0, bytesToWrite);
			m_videoIqCaptureBytes += bytesToWrite;
			if (m_videoIqCaptureBytes >= MaximumVideoIqCaptureBytes)
			{
				m_videoIqCaptureStream.Flush();
			}
		}
	}

	private static bool IsLikelyVideoIqActive(byte[] iq)
	{
		for (int offset = 0; offset + 3 < iq.Length; offset += 4)
		{
			short i = BitConverter.ToInt16(iq, offset);
			short q = BitConverter.ToInt16(iq, offset + 2);
			if (Math.Abs((int)i) >= VideoActiveRxPeakThreshold ||
				Math.Abs((int)q) >= VideoActiveRxPeakThreshold)
			{
				return true;
			}
		}
		return false;
	}

	private void QueueVideoDecodeFrame(byte[] iq, int frameId)
	{
		if (frameId == m_lastQueuedVideoRxFrameId)
		{
			return;
		}

		ushort targetPacing = 50;
		int queueCount = m_videoDecodeQueue.Count;
		if (queueCount >= 10)
		{
			targetPacing = 300;
		}
		else if (queueCount >= 6)
		{
			targetPacing = 180;
		}
		else if (queueCount >= 3)
		{
			targetPacing = 100;
		}

		long now = Environment.TickCount64;
		if (targetPacing != m_lastSentPacingUs && now - m_lastPacingChangeTick > 100)
		{
			SendPacingCommand(targetPacing);
			m_lastSentPacingUs = targetPacing;
			m_lastPacingChangeTick = now;
		}

		while (m_videoDecodeQueue.Count >= VideoDecodeQueueLimit &&
			m_videoDecodeQueue.TryDequeue(out _))
		{
			m_videoDecodeQueueDropCount++;
		}
		m_videoDecodeQueue.Enqueue((iq, frameId));
		m_lastQueuedVideoRxFrameId = frameId;
		m_videoGateQueuedFrameCount++;
		m_videoDecodeEvent.Set();
	}

	private string BuildVideoRecoverySummary()
	{
		lock (m_videoDiagnosticsLock)
		{
			int trackedFrames = m_videoRecoveryStats.Count;
			if (trackedFrames == 0)
			{
				return
					"首帧恢复耗时：--\n" +
					"最近完整帧：--\n" +
					"近完整未完成帧：0\n" +
					"平均恢复耗时：--\n";
			}

			var completedFrames = m_videoRecoveryStats.Values
				.Where(stats => stats.CompletedMs >= 0)
				.ToArray();
			int nearCompletePending = m_videoRecoveryStats.Values.Count(stats =>
				stats.CompletedMs < 0 &&
				stats.FirstNearCompleteMs >= 0);
			double averageLatency = completedFrames.Length == 0
				? -1.0
				: completedFrames.Average(stats => stats.CompletedMs - stats.FirstSeenMs);
			double completedRatio = trackedFrames == 0
				? 0.0
				: completedFrames.Length * 100.0 / trackedFrames;
			string firstRecoveredText = m_videoFirstRecoveredFrameMs < 0
				? "--"
				: $"{m_videoFirstRecoveredFrameMs / 1000.0:F2} s";
			string averageText = averageLatency < 0.0
				? "--"
				: $"{averageLatency / 1000.0:F2} s";
			string lastFrameText = completedFrames.Length == 0
				? "--"
				: $"{m_videoLastRecoveredFrameId}";
			return
				$"首帧恢复耗时：{firstRecoveredText}\n" +
				$"最近完整帧：{lastFrameText}\n" +
				$"近完整未完成帧：{nearCompletePending}\n" +
				$"帧恢复率：{completedFrames.Length}/{trackedFrames} ({completedRatio:F1}%)\n" +
				$"平均恢复耗时：{averageText}\n";
		}
	}

	private void UpdateVideoModemWaitingStatus(int rxFrameId, double correlation)
	{
		long packetCount;
		long completeFrameCount;
		long droppedFrameCount;
		long sampleCount;
		long decodeQueueDropCount;
		long gateQueuedFrameCount;
		long gateSkippedFrameCount;
		int decodeQueueDepth;
		int bufferedSamples;
		double normalCorrelation;
		double conjugateCorrelation;
		double rms;
		double peak;
		double meanI;
		double meanQ;
		double rmsI;
		double rmsQ;
		double imbalanceDb;
		double iqCorrelation;
		double coarseFrequencyHz;
		double clippingPercent;
		double zeroPercent;
		string metricsFile;
		string iqCaptureFile;
		string txManifestFile;
		string recoveryFile;
		string reassemblyStatus;
		string recoverySummary;
		int encodedBytes;
		int encodedWidth;
		int encodedHeight;
		int encodedChunks;

		lock (m_videoRxStatsLock)
		{
			long now = Environment.TickCount64;
			if (now - m_lastVideoDiagnosticsTick < 250)
			{
				return;
			}
			m_lastVideoDiagnosticsTick = now;

			packetCount = m_rxIqPacketCount;
			completeFrameCount = m_rxIqCompleteFrameCount;
			droppedFrameCount = m_rxIqDroppedFrameCount;
			sampleCount = m_rxIqSampleCount;
			decodeQueueDropCount = m_videoDecodeQueueDropCount;
			gateQueuedFrameCount = m_videoGateQueuedFrameCount;
			gateSkippedFrameCount = m_videoGateSkippedFrameCount;
			decodeQueueDepth = m_videoDecodeQueue.Count;
			bufferedSamples = m_qpskStreamDecoder.BufferedSamples;
			normalCorrelation = m_qpskStreamDecoder.LastNormalCorrelation;
			conjugateCorrelation = m_qpskStreamDecoder.LastConjugateCorrelation;
			rms = m_videoRxRms;
			peak = m_videoRxPeak;
			meanI = m_videoRxMeanI;
			meanQ = m_videoRxMeanQ;
			rmsI = m_videoRxRmsI;
			rmsQ = m_videoRxRmsQ;
			imbalanceDb = m_videoRxIqImbalanceDb;
			iqCorrelation = m_videoRxIqCorrelation;
			coarseFrequencyHz = m_videoRxCoarseFrequencyHz;
			clippingPercent = m_videoRxClippingPercent;
			zeroPercent = m_videoRxZeroPercent;
			metricsFile = Path.GetFileName(m_videoMetricsPath);
			iqCaptureFile = Path.GetFileName(m_videoIqCapturePath);
			txManifestFile = Path.GetFileName(m_videoTxManifestPath);
			recoveryFile = Path.GetFileName(m_videoRecoveryPath);
			reassemblyStatus = m_videoReassembler.LatestStatus;
			recoverySummary = BuildVideoRecoverySummary();
			encodedBytes = m_videoLastEncodedBytes;
			encodedWidth = m_videoLastEncodedWidth;
			encodedHeight = m_videoLastEncodedHeight;
			encodedChunks = m_videoLastEncodedChunks;
		}

		string encodedSizeText = encodedWidth > 0 && encodedHeight > 0
			? $"{encodedWidth}x{encodedHeight}"
			: "--";
		string txEncodeSummary =
			$"发送长边：{VideoTxMaxLongEdge} px\n" +
			$"WebP质量：{VideoTxWebPQuality}%\n" +
			$"最近发送尺寸：{encodedSizeText}\n" +
			$"最近WebP大小：{encodedBytes} bytes\n" +
			$"最近预计分片：{encodedChunks}\n";

		((DispatcherObject)this).Dispatcher.BeginInvoke((Action)delegate
		{
			tbRadarData.Text =
				$"QPSK 视频 E310 链路\n\n" +
				$"状态：已收到 RX IQ，正在搜索 QPSK 前导\n" +
				$"当前 RX IQ 帧：{rxFrameId}\n" +
				$"UDP IQ 数据包：{packetCount}\n" +
				$"完整 IQ 帧：{completeFrameCount}\n" +
				$"不完整/丢失 IQ 帧：{droppedFrameCount}\n" +
				$"累计 RX 样点：{sampleCount}\n" +
				$"解调队列：{decodeQueueDepth}/{VideoDecodeQueueLimit}\n" +
				$"解调队列丢帧：{decodeQueueDropCount}\n" +
				$"RX门控入队帧：{gateQueuedFrameCount}\n" +
				$"RX门控跳过帧：{gateSkippedFrameCount}\n" +
				$"RX门控峰值阈值：{VideoActiveRxPeakThreshold} ADC\n" +
				$"QPSK 流缓冲：{bufferedSamples} 样点\n" +
				$"正常 IQ 相关：{normalCorrelation:F4}\n" +
				$"共轭 IQ 相关：{conjugateCorrelation:F4}\n" +
				$"最佳前导相关：{correlation:F4}（解调门限 {QpskModem.PreambleThreshold:F2}）\n" +
				$"RX RMS（12位FS）：{rms:F5}\n" +
				$"RX 峰值（12位FS）：{peak:F5}\n" +
				$"I/Q 均值：{meanI:+0.000000;-0.000000;0.000000} / " +
					$"{meanQ:+0.000000;-0.000000;0.000000}\n" +
				$"I/Q RMS：{rmsI:F6} / {rmsQ:F6}\n" +
				$"I/Q 功率失衡：{imbalanceDb:+0.00;-0.00;0.00} dB\n" +
				$"I/Q 相关系数：{iqCorrelation:+0.000;-0.000;0.000}\n" +
				$"粗频偏估计：{coarseFrequencyHz:+0.0;-0.0;0.0} Hz\n" +
				$"削顶样点：{clippingPercent:F4}%\n" +
				$"全零样点：{zeroPercent:F4}%\n\n" +
				$"累计 TX 分片：{m_videoModemChunkCount}\n" +
				$"其中选择性补发：{m_videoModemRepairChunkCount}\n" +
				$"发送源帧：{m_videoModemSentFrameCount}\n" +
				$"接收确认完整帧：{m_videoModemConfirmedFrameCount}\n" +
				$"累计 RX 分片：{m_videoModemDecodedChunkCount}\n" +
				$"恢复视频帧：{m_videoModemRecoveredFrameCount}\n" +
				$"丢弃迟到显示帧：{m_videoStaleDisplayFrameCount}\n" +
				recoverySummary +
				txEncodeSummary +
				$"CRC/格式失败：{m_videoModemFailureCount}\n" +
				$"  CRC fail: {m_videoWirelessCrcFailureCount}\n" +
				$"  FORMAT fail: {m_videoWirelessFormatFailureCount}\n\n" +
				$"采样率：3.84 MSPS\n" +
				$"符号率：{3840 / QpskModem.SamplesPerSymbol} ksym/s\n" +
				$"分片负载：{VideoWirelessChunkPayloadBytes} bytes\n" +
				$"快速筛选门限：{QpskModem.QuickThreshold:F2}\n" +
				$"前导搜索步进：{QpskModem.PreambleSearchStep} samples\n" +
				$"调制：QPSK，{QpskModem.SamplesPerSymbol} samples/symbol\n\n" +
				$"CSV：diagnostics\\{metricsFile}\n" +
				$"原始IQ：diagnostics\\{iqCaptureFile}\n" +
				$"TX清单：diagnostics\\{txManifestFile}\n" +
				$"恢复日志：diagnostics\\{recoveryFile}";
			tbRadarData.Text += $"\n\n分片重组：{reassemblyStatus}";
			if (!string.IsNullOrWhiteSpace(m_videoLastCandidateDiagnostic))
			{
				tbRadarData.Text += $"\n\n{m_videoLastCandidateDiagnostic}";
			}
			if (!string.IsNullOrWhiteSpace(m_videoLastParseFailureReason))
			{
				tbRadarData.Text += $"\n{m_videoLastParseFailureReason}";
			}
			if (!string.IsNullOrWhiteSpace(m_videoLastDemodDiagnostic))
			{
				tbRadarData.Text += $"\n\n{m_videoLastDemodDiagnostic}";
			}
		});
	}

	private static string BuildFmcwCpiText(FmcwProcessor.CpiResult result, int lastFrameId)
	{
		StringWriter writer = new StringWriter();
		writer.WriteLine("【发送波形（TX Waveform）- 上位机发往 E310 TX1 的基带 IQ】");
		writer.Write(BuildTxWaveformText(3, -1, WaveformSamplesPerFrame * 4, WaveformSamplesPerFrame));
		writer.WriteLine();
		writer.WriteLine();
		writer.WriteLine("【回传采集（RX IQ）- E310 RX1 回传到 PC 的原始 IQ 帧】");
		writer.WriteLine($"相干处理批次（CPI）：{result.CpiIndex}");
		writer.WriteLine($"最后回传帧号（RX Frame）：{lastFrameId}");
		writer.WriteLine($"累计扫频数（Chirps）：{result.ChirpCount}");
		writer.WriteLine($"每周期采样点（Samples/PRT）：{result.SamplesPerChirp}");
		writer.WriteLine($"有效扫频点数（Active Samples）：{result.ActiveSampleCount}");
		writer.WriteLine();
		writer.WriteLine("【回环校准（Loopback Calibration）- RX 空闲段同步与过载诊断】");
		writer.WriteLine($"同步状态（Sync）：{result.Diagnostics.SyncStatus}");
		writer.WriteLine($"对齐偏移（Sync Offset）：{result.Diagnostics.SyncOffsetSamples} samples");
		writer.WriteLine($"空闲起点（Idle Start）：{result.Diagnostics.IdleStartSample} samples");
		writer.WriteLine($"空闲长度（Idle Length）：{result.Diagnostics.IdleSampleCount} samples");
		writer.WriteLine($"TX 模板相关（Correlation）：{result.Diagnostics.CorrelationScore:F3}");
		writer.WriteLine($"扫频锁定（Sweep Lock）：{(result.Diagnostics.SweepLocked ? "是" : "否")}");
		writer.WriteLine($"扫频方向（Direction）：{result.Diagnostics.SweepDirection}");
		writer.WriteLine($"扫频范围（Sweep）：{result.Diagnostics.SweepStartRfHz / 1e6:F3} - {result.Diagnostics.SweepEndRfHz / 1e6:F3} MHz");
		writer.WriteLine($"扫频带宽（Bandwidth）：{result.Diagnostics.SweepBandwidthHz / 1e6:F3} MHz");
		writer.WriteLine($"有效扫频（Active）：{result.Diagnostics.SweepActiveTimeSeconds * 1e6:F2} us");
		writer.WriteLine($"扫频线性误差（Linearity）：{result.Diagnostics.SweepLinearityRmsHz / 1e3:F1} kHz");
		writer.WriteLine($"有效值（RMS）：{result.Diagnostics.Rms:F4}");
		writer.WriteLine($"峰值（Peak）：{result.Diagnostics.Peak:F4}");
		writer.WriteLine($"直流偏置（DC Offset）：I={result.Diagnostics.MeanI:+0.0000;-0.0000;0.0000}, Q={result.Diagnostics.MeanQ:+0.0000;-0.0000;0.0000}");
		writer.WriteLine($"削顶比例（Clipping）：{result.Diagnostics.ClippingPercent:F2}%");
		writer.WriteLine($"零值比例（Zero Samples）：{result.Diagnostics.ZeroPercent:F2}%");
		writer.WriteLine();
		writer.WriteLine("【信号处理（DSP）- 混频、滤波、距离 FFT 与速度二次 FFT 参数】");
		writer.WriteLine("混频方式（Mixer）：RX IQ × conj(TX IQ)");
		writer.WriteLine("低通滤波（LPF）：65 阶 Hann-windowed sinc，截止频率 2 MHz");
		writer.WriteLine($"距离 FFT（Range FFT）：{result.RangeFftLength} 点");
		writer.WriteLine($"速度二次 FFT（Doppler FFT / 2nd FFT）：{result.DopplerFftLength} 点");
		writer.WriteLine($"距离分辨率（Range Resolution）：{result.RangeResolutionMeters:F2} m");
		writer.WriteLine($"速度分辨率（Velocity Bin）：{result.VelocityResolutionMetersPerSecond:F2} m/s");
		writer.WriteLine("回环显示范围（Loopback View）：优先显示 0-200 m、近零速度区域");
		List<FmcwProcessor.Target> trustedTargets = GetTrustedFmcwTargets(result).ToList();
		bool fmcwLocked = IsFmcwLocked(result);
		if (!fmcwLocked)
		{
			writer.WriteLine();
			writer.WriteLine("【目标检测（Detection）- Range-Doppler 峰值检测结果】");
			writer.WriteLine("检测目标数（Targets）：0");
			writer.WriteLine("结果说明（Result）：当前 TX/RX 相关度过低或 RX 幅度过弱，暂不显示噪声峰目标。");
		}
		else if (trustedTargets.Count == 0)
		{
			writer.WriteLine();
			writer.WriteLine("【目标检测（Detection）- Range-Doppler 峰值检测结果】");
			writer.WriteLine("检测目标数（Targets）：0");
			writer.WriteLine("结果说明（Result）：未发现超过阈值的目标峰值");
		}
		else
		{
			writer.WriteLine();
			writer.WriteLine("【目标检测（Detection）- Range-Doppler 峰值检测结果】");
			writer.WriteLine($"检测目标数（Targets）：{trustedTargets.Count}");
			for (int i = 0; i < trustedTargets.Count; i++)
			{
				var target = trustedTargets[i];
				writer.WriteLine(
					$"目标 {i + 1}（Target {i + 1}）：距离（R）={target.DistanceMeters:F1} m，" +
					$"速度（V）={target.VelocityMetersPerSecond:+0.0;-0.0;0.0} m/s，" +
					$"信噪比（SNR）={target.SnrDb:F1} dB，" +
					$"强度（Power）={target.PowerDb:F1} dB");
			}
		}
		writer.WriteLine();
		writer.WriteLine("【状态说明（Note）- 当前算法假设】");
		writer.WriteLine("当前版本按 E310 回传帧边界作为扫频周期边界，尚未加入硬件帧同步。");
		return writer.ToString();
	}

	private static string BuildFmcwLiveStatusText(
		int completedCpiIndex,
		int collectingChirps,
		int lastFrameId,
		string lastCpiText)
	{
		StringWriter writer = new StringWriter();
		writer.WriteLine("【实时状态（Live Status）- FMCW 混频处理 Mode 3】");
		writer.WriteLine($"最新完成 CPI（Completed CPI）：{completedCpiIndex}");
		writer.WriteLine($"当前累计扫频帧（Collecting Chirps）：{collectingChirps}/64");
		writer.WriteLine($"最后回传帧号（RX Frame）：{lastFrameId}");
		writer.WriteLine();
		if (string.IsNullOrEmpty(lastCpiText))
		{
			writer.WriteLine("正在等待第一组 64 帧 CPI 完成...");
			writer.WriteLine("完成后会显示混频后时序图、距离 FFT、速度二次 FFT 和目标检测结果。");
		}
		else
		{
			writer.WriteLine("【上一组完整 CPI 结果（Last Completed CPI）】");
			writer.Write(lastCpiText);
		}
		return writer.ToString();
	}

	private static string BuildTxWaveformText(byte mode, int frameId, int iqBytes, int sampleCount)
	{
		StringWriter writer = new StringWriter();
		if (frameId >= 0)
		{
			writer.WriteLine($"波形帧号（TX Frame）：{frameId}");
		}
		writer.WriteLine($"波形模式（Mode）：{WaveformGenerator.GetModeName(mode)}");
		writer.WriteLine($"IQ 数据大小（IQ Bytes）：{iqBytes} 字节");
		writer.WriteLine($"IQ 采样点（IQ Samples）：{sampleCount}");
		writer.WriteLine($"采样率（Fs）：{WaveformGenerator.SampleRateHz / 1e6:F2} MSPS");
		writer.WriteLine($"中心频率（LO）：200.000 MHz");
		writer.WriteLine($"扫频带宽（BW）：{WaveformGenerator.FmcwBandwidthHz / 1e6:F2} MHz");
		writer.WriteLine($"周期时间（PRT）：{sampleCount / WaveformGenerator.SampleRateHz * 1e6:F2} us");
		writer.WriteLine($"空闲时间（Idle）：{WaveformGenerator.FmcwIdleTimeSeconds * 1e6:F2} us");
		writer.WriteLine($"有效扫频时间（Active Chirp）：{WaveformGenerator.GetFmcwChirpSamples(sampleCount) / WaveformGenerator.SampleRateHz * 1e6:F2} us");
		writer.WriteLine($"包类型（Packet Type）：0x05");
		return writer.ToString();
	}

	private static BitmapSource RenderFmcwCpiResult(FmcwProcessor.CpiResult result)
	{
		const int width = 960;
		const int height = 720;
		const int left = 74;
		const int right = 28;
		const int beatTop = 58;
		const int beatBottom = 188;
		const int rangeTop = 244;
		const int rangeBottom = 350;
		const int heatTop = 412;
		const int heatBottom = 682;

		using Mat canvas = new Mat(height, width, MatType.CV_8UC3, new Scalar(18, 20, 24));
		Scalar grid = new Scalar(54, 58, 66);
		Scalar text = new Scalar(215, 220, 228);
		Cv2.PutText(canvas, "FMCW CPI: beat, range FFT, range-Doppler FFT",
			new OpenCvSharp.Point(left, 23),
			HersheyFonts.HersheySimplex, 0.55, text, 1, LineTypes.AntiAlias);

		DrawPanelFrame(canvas, left, beatTop, width - right, beatBottom, grid, text, "Dechirped IF beat I/Q after mixer + LPF");
		DrawPanelFrame(canvas, left, rangeTop, width - right, rangeBottom, grid, text, "Range FFT spectrum");
		DrawPanelFrame(canvas, left, heatTop, width - right, heatBottom, grid, text, "Range-Doppler radar map");
		DrawBeatTrace(canvas, result, left, width - right, beatTop, beatBottom);
		DrawRangeTrace(canvas, result, left, width - right, rangeTop, rangeBottom);
		DrawRangeDoppler(canvas, result, left, width - right, heatTop, heatBottom);
		DrawFmcwTargets(canvas, result, left, width - right, heatTop, heatBottom);
		DrawFmcwLockStatus(canvas, result, left, beatTop, width - right);

		int trustedCount = GetTrustedFmcwTargets(result).Count();
		Cv2.PutText(canvas, $"CPI {result.CpiIndex}  chirps={result.ChirpCount}  trusted={trustedCount}  raw={result.Targets.Count}",
			new OpenCvSharp.Point(left, height - 10),
			HersheyFonts.HersheySimplex, 0.47, text, 1, LineTypes.AntiAlias);
		BitmapSource bitmap = canvas.ToBitmapSource();
		bitmap.Freeze();
		return bitmap;
	}

	private static void DrawPanelFrame(Mat canvas, int x0, int y0, int x1, int y1, Scalar grid, Scalar text, string title)
	{
		Cv2.Rectangle(canvas, new OpenCvSharp.Rect(x0, y0, x1 - x0, y1 - y0), grid, 1);
		for (int i = 1; i < 4; i++)
		{
			int y = y0 + i * (y1 - y0) / 4;
			Cv2.Line(canvas, new OpenCvSharp.Point(x0, y), new OpenCvSharp.Point(x1, y), grid, 1);
		}
		for (int i = 1; i < 6; i++)
		{
			int x = x0 + i * (x1 - x0) / 6;
			Cv2.Line(canvas, new OpenCvSharp.Point(x, y0), new OpenCvSharp.Point(x, y1), grid, 1);
		}
		Cv2.PutText(canvas, title, new OpenCvSharp.Point(x0, y0 - 9),
			HersheyFonts.HersheySimplex, 0.45, text, 1, LineTypes.AntiAlias);
	}

	private static void DrawBeatTrace(Mat canvas, FmcwProcessor.CpiResult result, int x0, int x1, int y0, int y1)
	{
		int count = Math.Min(result.BeatI.Length, result.BeatQ.Length);
		if (count < 2)
		{
			return;
		}
		double maximum = 1e-9;
		for (int i = 0; i < count; i++)
		{
			maximum = Math.Max(maximum, Math.Abs(result.BeatI[i]));
			maximum = Math.Max(maximum, Math.Abs(result.BeatQ[i]));
		}
		int center = (y0 + y1) / 2;
		for (int pixel = 1; pixel < x1 - x0; pixel++)
		{
			int previous = (int)((long)(pixel - 1) * count / (x1 - x0));
			int current = (int)((long)pixel * count / (x1 - x0));
			DrawTraceSegment(canvas, x0 + pixel - 1, x0 + pixel, center,
				result.BeatI[previous], result.BeatI[current], maximum, y0, y1, new Scalar(90, 220, 255));
			DrawTraceSegment(canvas, x0 + pixel - 1, x0 + pixel, center,
				result.BeatQ[previous], result.BeatQ[current], maximum, y0, y1, new Scalar(255, 185, 90));
		}
	}

	private static void DrawRangeTrace(Mat canvas, FmcwProcessor.CpiResult result, int x0, int x1, int y0, int y1)
	{
		if (result.RangeDb.Length < 2)
		{
			return;
		}
		int maxRangeBin = GetLoopbackDisplayRangeBinCount(result);
		double minDb = Percentile(result.RangeDb.Take(maxRangeBin).ToArray(), 0.10);
		double maxDb = result.RangeDb.Take(maxRangeBin).Max();
		minDb = Math.Min(minDb, maxDb - 12.0);
		double span = Math.Max(1.0, maxDb - minDb);
		int plotWidth = x1 - x0;
		int barWidth = Math.Max(2, plotWidth / maxRangeBin);
		for (int bin = 0; bin < maxRangeBin; bin++)
		{
			double normalized = Math.Clamp((result.RangeDb[bin] - minDb) / span, 0.0, 1.0);
			int barX = x0 + bin * plotWidth / maxRangeBin;
			int nextX = x0 + (bin + 1) * plotWidth / maxRangeBin;
			int barY = y1 - (int)Math.Round(normalized * (y1 - y0 - 2));
			int width = Math.Max(1, Math.Min(barWidth, nextX - barX - 1));
			Scalar fill = normalized > 0.82 ? new Scalar(45, 235, 255) : new Scalar(105, 210, 135);
			Cv2.Rectangle(canvas, new OpenCvSharp.Rect(barX, barY, width, y1 - barY), fill, -1);
		}
		Cv2.Line(canvas, new OpenCvSharp.Point(x0, y1), new OpenCvSharp.Point(x1, y1),
			new Scalar(92, 98, 108), 1, LineTypes.AntiAlias);
		Cv2.PutText(canvas, $"0 - {result.RangeMeters[maxRangeBin - 1]:F0} m",
			new OpenCvSharp.Point(x0 + 8, y1 - 9),
			HersheyFonts.HersheySimplex, 0.42, new Scalar(210, 215, 220), 1, LineTypes.AntiAlias);
	}

	private static void DrawRangeDoppler(Mat canvas, FmcwProcessor.CpiResult result, int x0, int x1, int y0, int y1)
	{
		int dopplerBins = result.RangeDopplerDb.GetLength(0);
		int rangeBins = GetLoopbackDisplayRangeBinCount(result);
		int zeroDopplerBin = GetZeroDopplerBin(result);
		double minDb = double.MaxValue;
		double maxDb = double.MinValue;
		for (int y = 0; y < dopplerBins; y++)
		{
			for (int x = 1; x < rangeBins; x++)
			{
				if (Math.Abs(y - zeroDopplerBin) <= 1)
				{
					continue;
				}
				double value = result.RangeDopplerDb[y, x];
				minDb = Math.Min(minDb, value);
				maxDb = Math.Max(maxDb, value);
			}
		}
		if (minDb == double.MaxValue || maxDb == double.MinValue)
		{
			minDb = result.RangeDopplerDb.Cast<double>().Min();
			maxDb = result.RangeDopplerDb.Cast<double>().Max();
		}
		minDb = Math.Max(minDb, maxDb - 42.0);
		double span = Math.Max(1.0, maxDb - minDb);
		for (int py = y0; py < y1; py++)
		{
			int doppler = Math.Clamp((py - y0) * dopplerBins / (y1 - y0), 0, dopplerBins - 1);
			for (int px = x0; px < x1; px++)
			{
				int range = Math.Clamp((px - x0) * rangeBins / (x1 - x0), 0, rangeBins - 1);
				double value = result.RangeDopplerDb[doppler, range];
				if (Math.Abs(doppler - zeroDopplerBin) <= 1)
				{
					value = Math.Min(value, maxDb);
				}
				double normalized = Math.Clamp((value - minDb) / span, 0.0, 1.0);
				canvas.Set(py, px, RadarHeatColor(normalized));
			}
		}
		int zeroVelocityY = y0 + zeroDopplerBin * (y1 - y0) / dopplerBins;
		Cv2.Line(canvas, new OpenCvSharp.Point(x0, zeroVelocityY), new OpenCvSharp.Point(x1, zeroVelocityY),
			new Scalar(155, 170, 190), 1, LineTypes.AntiAlias);
		DrawRangeDopplerAxes(canvas, result, x0, x1, y0, y1, rangeBins, zeroDopplerBin);
		Cv2.PutText(canvas, $"RD {dopplerBins} x {rangeBins} bins  color scale excludes V=0 leakage",
			new OpenCvSharp.Point(x0 + 8, y0 + 16),
			HersheyFonts.HersheySimplex, 0.38, new Scalar(225, 230, 235), 1, LineTypes.AntiAlias);
		Cv2.PutText(canvas, $"R max {result.RangeMeters[rangeBins - 1]:F0} m",
			new OpenCvSharp.Point(x0 + 8, y1 - 10),
			HersheyFonts.HersheySimplex, 0.42, new Scalar(230, 230, 235), 1, LineTypes.AntiAlias);
	}

	private static void DrawRangeDopplerAxes(
		Mat canvas,
		FmcwProcessor.CpiResult result,
		int x0,
		int x1,
		int y0,
		int y1,
		int rangeBins,
		int zeroDopplerBin)
	{
		Scalar axis = new Scalar(150, 160, 175);
		Scalar text = new Scalar(225, 230, 235);
		int dopplerBins = result.RangeDopplerDb.GetLength(0);
		for (int i = 0; i <= 2; i++)
		{
			int rangeBin = i == 0 ? 0 : i == 1 ? rangeBins / 2 : rangeBins - 1;
			int x = x0 + rangeBin * (x1 - x0) / rangeBins;
			Cv2.Line(canvas, new OpenCvSharp.Point(x, y1 - 5), new OpenCvSharp.Point(x, y1), axis, 1);
			Cv2.PutText(canvas, $"{result.RangeMeters[rangeBin]:F0}m",
				new OpenCvSharp.Point(Math.Min(x + 4, x1 - 48), y1 - 27),
				HersheyFonts.HersheySimplex, 0.34, text, 1, LineTypes.AntiAlias);
		}
		int[] dopplerMarks = new[] { 0, zeroDopplerBin, dopplerBins - 1 };
		foreach (int dopplerBin in dopplerMarks)
		{
			int y = y0 + dopplerBin * (y1 - y0) / dopplerBins;
			double velocity = result.DopplerVelocityMetersPerSecond[dopplerBin];
			Cv2.Line(canvas, new OpenCvSharp.Point(x0, y), new OpenCvSharp.Point(x0 + 5, y), axis, 1);
			Cv2.PutText(canvas, $"{velocity:+0;-0;0}m/s",
				new OpenCvSharp.Point(x0 + 8, Math.Clamp(y - 4, y0 + 32, y1 - 44)),
				HersheyFonts.HersheySimplex, 0.34, text, 1, LineTypes.AntiAlias);
		}
	}

	private static void DrawFmcwTargets(Mat canvas, FmcwProcessor.CpiResult result, int x0, int x1, int y0, int y1)
	{
		int dopplerBins = result.RangeDopplerDb.GetLength(0);
		int rangeBins = GetLoopbackDisplayRangeBinCount(result);
		var trustedTargets = GetTrustedFmcwTargets(result)
			.Where(target => target.RangeBin < rangeBins);
		foreach (var target in trustedTargets)
		{
			int x = x0 + target.RangeBin * (x1 - x0) / rangeBins;
			int y = y0 + target.DopplerBin * (y1 - y0) / dopplerBins;
			Scalar color = new Scalar(40, 245, 255);
			Cv2.Circle(canvas, new OpenCvSharp.Point(x, y), 9, color, 2, LineTypes.AntiAlias);
			Cv2.Line(canvas, new OpenCvSharp.Point(x - 13, y), new OpenCvSharp.Point(x + 13, y), color, 1, LineTypes.AntiAlias);
			Cv2.Line(canvas, new OpenCvSharp.Point(x, y - 13), new OpenCvSharp.Point(x, y + 13), color, 1, LineTypes.AntiAlias);
			Cv2.PutText(canvas,
				$"R={target.DistanceMeters:F1}m V={target.VelocityMetersPerSecond:+0.0;-0.0;0.0}m/s",
				new OpenCvSharp.Point(Math.Min(x + 12, x1 - 210), Math.Max(y - 8, y0 + 16)),
				HersheyFonts.HersheySimplex, 0.4, color, 1, LineTypes.AntiAlias);
		}
	}

	private static void DrawFmcwLockStatus(Mat canvas, FmcwProcessor.CpiResult result, int x0, int y0, int x1)
	{
		bool iqLocked = IsFmcwIqLocked(result);
		bool sweepLocked = result.Diagnostics.SweepLocked;
		bool locked = iqLocked || sweepLocked;
		string mode = iqLocked ? "IQ LOCK" : sweepLocked ? "SWEEP LOCK" : "NO LOCK";
		string label = $"{mode}  corr={result.Diagnostics.CorrelationScore:F3}  bw={result.Diagnostics.SweepBandwidthHz / 1e6:F1}MHz";
		Scalar fill = iqLocked ? new Scalar(44, 118, 72) : sweepLocked ? new Scalar(54, 94, 126) : new Scalar(36, 54, 128);
		Scalar border = locked ? new Scalar(95, 220, 130) : new Scalar(70, 130, 255);
		OpenCvSharp.Size textSize = Cv2.GetTextSize(label, HersheyFonts.HersheySimplex, 0.48, 1, out int baseline);
		int width = textSize.Width + 22;
		int height = textSize.Height + baseline + 12;
		int x = Math.Max(x0, x1 - width - 10);
		int y = y0 + 8;
		Cv2.Rectangle(canvas, new OpenCvSharp.Rect(x, y, width, height), fill, -1);
		Cv2.Rectangle(canvas, new OpenCvSharp.Rect(x, y, width, height), border, 1, LineTypes.AntiAlias);
		Cv2.PutText(canvas, label, new OpenCvSharp.Point(x + 11, y + textSize.Height + 6),
			HersheyFonts.HersheySimplex, 0.48, new Scalar(235, 242, 248), 1, LineTypes.AntiAlias);
	}

	private static IEnumerable<FmcwProcessor.Target> GetTrustedFmcwTargets(FmcwProcessor.CpiResult result)
	{
		if (!IsFmcwIqLocked(result))
		{
			return Enumerable.Empty<FmcwProcessor.Target>();
		}
		double velocityGate = Math.Max(15.0, result.VelocityResolutionMetersPerSecond * 1.5);
		return result.Targets
			.Where(target =>
				target.DistanceMeters <= 100.0 &&
				Math.Abs(target.VelocityMetersPerSecond) <= velocityGate)
			.Take(3);
	}

	private static bool IsFmcwLocked(FmcwProcessor.CpiResult result)
	{
		return IsFmcwIqLocked(result) || result.Diagnostics.SweepLocked;
	}

	private static bool IsFmcwIqLocked(FmcwProcessor.CpiResult result)
	{
		return result.Diagnostics.CorrelationScore >= 0.08 &&
			result.Diagnostics.Rms >= 0.0008 &&
			result.Diagnostics.ClippingPercent < 5.0;
	}

	private static int GetLoopbackDisplayRangeBinCount(FmcwProcessor.CpiResult result)
	{
		const double maxDisplayRangeMeters = 200.0;
		int count = 2;
		while (count < result.RangeMeters.Length &&
			result.RangeMeters[count] <= maxDisplayRangeMeters)
		{
			count++;
		}
		return Math.Clamp(count, 2, result.RangeMeters.Length);
	}

	private static int GetZeroDopplerBin(FmcwProcessor.CpiResult result)
	{
		int best = 0;
		double bestAbsVelocity = double.MaxValue;
		for (int i = 0; i < result.DopplerVelocityMetersPerSecond.Length; i++)
		{
			double absVelocity = Math.Abs(result.DopplerVelocityMetersPerSecond[i]);
			if (absVelocity < bestAbsVelocity)
			{
				bestAbsVelocity = absVelocity;
				best = i;
			}
		}
		return best;
	}

	private static void DrawTraceSegment(
		Mat canvas,
		int x0,
		int x1,
		int center,
		double previous,
		double current,
		double maximum,
		int y0,
		int y1,
		Scalar color)
	{
		double scale = (y1 - y0) * 0.45 / maximum;
		int yPrevious = Math.Clamp(center - (int)Math.Round(previous * scale), y0, y1);
		int yCurrent = Math.Clamp(center - (int)Math.Round(current * scale), y0, y1);
		Cv2.Line(canvas, new OpenCvSharp.Point(x0, yPrevious),
			new OpenCvSharp.Point(x1, yCurrent), color, 1, LineTypes.AntiAlias);
	}

	private static Vec3b RadarHeatColor(double value)
	{
		value = Math.Clamp(value, 0.0, 1.0);
		if (value < 0.25)
		{
			double t = value / 0.25;
			return LerpColor(new Vec3b(24, 14, 8), new Vec3b(105, 45, 14), t);
		}
		if (value < 0.55)
		{
			double t = (value - 0.25) / 0.30;
			return LerpColor(new Vec3b(105, 45, 14), new Vec3b(230, 210, 35), t);
		}
		if (value < 0.82)
		{
			double t = (value - 0.55) / 0.27;
			return LerpColor(new Vec3b(230, 210, 35), new Vec3b(45, 245, 255), t);
		}
		return LerpColor(new Vec3b(45, 245, 255), new Vec3b(35, 80, 255), (value - 0.82) / 0.18);
	}

	private static Vec3b LerpColor(Vec3b a, Vec3b b, double t)
	{
		t = Math.Clamp(t, 0.0, 1.0);
		return new Vec3b(
			(byte)Math.Round(a.Item0 + (b.Item0 - a.Item0) * t),
			(byte)Math.Round(a.Item1 + (b.Item1 - a.Item1) * t),
			(byte)Math.Round(a.Item2 + (b.Item2 - a.Item2) * t));
	}

	private static double Percentile(double[] values, double percentile)
	{
		if (values.Length == 0)
		{
			return 0.0;
		}
		Array.Sort(values);
		int index = Math.Clamp((int)Math.Round((values.Length - 1) * percentile), 0, values.Length - 1);
		return values[index];
	}

	private static BitmapSource RenderFmcwTrace(
		RxSignalAnalyzer.ChirpTrace trace,
		string title)
	{
		const int width = 960;
		const int height = 430;
		const int left = 72;
		const int right = 24;
		const int frequencyTop = 34;
		const int frequencyBottom = 250;
		const int magnitudeTop = 294;
		const int magnitudeBottom = 394;
		const double minFrequencyMHz = 185.0;
		const double maxFrequencyMHz = 215.0;

		using Mat canvas = new Mat(height, width, MatType.CV_8UC3, new Scalar(20, 22, 26));
		Scalar grid = new Scalar(58, 62, 70);
		Scalar text = new Scalar(205, 210, 220);
		Scalar frequencyColor = new Scalar(70, 220, 255);
		Scalar magnitudeColor = new Scalar(110, 230, 120);
		Scalar idleColor = new Scalar(75, 80, 90);
		int plotWidth = width - left - right;

		for (int i = 0; i <= 5; i++)
		{
			int x = left + i * plotWidth / 5;
			Cv2.Line(canvas, new OpenCvSharp.Point(x, frequencyTop),
				new OpenCvSharp.Point(x, magnitudeBottom), grid, 1);
			Cv2.PutText(canvas, $"{i * 40} us",
				new OpenCvSharp.Point(x - 20, height - 10),
				HersheyFonts.HersheySimplex, 0.42, text, 1, LineTypes.AntiAlias);
		}
		for (int i = 0; i <= 3; i++)
		{
			double frequency = minFrequencyMHz + i * 10.0;
			int y = frequencyBottom -
				(int)Math.Round((frequency - minFrequencyMHz) /
					(maxFrequencyMHz - minFrequencyMHz) * (frequencyBottom - frequencyTop));
			Cv2.Line(canvas, new OpenCvSharp.Point(left, y),
				new OpenCvSharp.Point(width - right, y), grid, 1);
			Cv2.PutText(canvas, $"{frequency:F0}",
				new OpenCvSharp.Point(30, y + 5),
				HersheyFonts.HersheySimplex, 0.44, text, 1, LineTypes.AntiAlias);
		}

		Cv2.PutText(canvas, title,
			new OpenCvSharp.Point(left, 22),
			HersheyFonts.HersheySimplex, 0.48, text, 1, LineTypes.AntiAlias);
		Cv2.PutText(canvas, "RF frequency (MHz)",
			new OpenCvSharp.Point(left, frequencyTop + 18),
			HersheyFonts.HersheySimplex, 0.44, text, 1, LineTypes.AntiAlias);
		Cv2.PutText(canvas, "IQ magnitude",
			new OpenCvSharp.Point(left, magnitudeTop - 10),
			HersheyFonts.HersheySimplex, 0.55, text, 1, LineTypes.AntiAlias);

		double maxMagnitude = Math.Max(trace.Magnitude.Max(), 1e-9);
		for (int i = 1; i < trace.TimeUs.Length; i++)
		{
			int x0 = left + (i - 1) * plotWidth / (trace.TimeUs.Length - 1);
			int x1 = left + i * plotWidth / (trace.TimeUs.Length - 1);
			bool active = i < trace.ActivePointCount;

			if (active)
			{
				int fy0 = frequencyBottom - (int)Math.Round(
					(trace.RfMHz[i - 1] - minFrequencyMHz) /
					(maxFrequencyMHz - minFrequencyMHz) * (frequencyBottom - frequencyTop));
				int fy1 = frequencyBottom - (int)Math.Round(
					(trace.RfMHz[i] - minFrequencyMHz) /
					(maxFrequencyMHz - minFrequencyMHz) * (frequencyBottom - frequencyTop));
				fy0 = Math.Clamp(fy0, frequencyTop, frequencyBottom);
				fy1 = Math.Clamp(fy1, frequencyTop, frequencyBottom);
				Cv2.Line(canvas, new OpenCvSharp.Point(x0, fy0),
					new OpenCvSharp.Point(x1, fy1), frequencyColor, 2, LineTypes.AntiAlias);
			}

			int my0 = magnitudeBottom - (int)Math.Round(
				trace.Magnitude[i - 1] / maxMagnitude * (magnitudeBottom - magnitudeTop));
			int my1 = magnitudeBottom - (int)Math.Round(
				trace.Magnitude[i] / maxMagnitude * (magnitudeBottom - magnitudeTop));
			Cv2.Line(canvas, new OpenCvSharp.Point(x0, my0),
				new OpenCvSharp.Point(x1, my1),
				active ? magnitudeColor : idleColor, 1, LineTypes.AntiAlias);
		}

		int idleX = left + trace.ActivePointCount * plotWidth / trace.TimeUs.Length;
		for (int y = frequencyTop; y < magnitudeBottom; y += 10)
		{
			Cv2.Line(canvas, new OpenCvSharp.Point(idleX, y),
				new OpenCvSharp.Point(idleX, Math.Min(y + 5, magnitudeBottom)),
				new Scalar(90, 120, 230), 1, LineTypes.AntiAlias);
		}
		Cv2.PutText(canvas, "idle",
			new OpenCvSharp.Point(Math.Min(idleX + 5, width - 55), frequencyTop + 18),
			HersheyFonts.HersheySimplex, 0.45, new Scalar(100, 150, 255), 1, LineTypes.AntiAlias);

		BitmapSource bitmap = canvas.ToBitmapSource();
		bitmap.Freeze();
		return bitmap;
	}

	private void ParseRadarPacket(byte[] packetData)
	{
		short target1Distance = BitConverter.ToInt16(packetData, 17);
		short target1Speed = BitConverter.ToInt16(packetData, 19);
		short target2Distance = BitConverter.ToInt16(packetData, 21);
		short target2Speed = BitConverter.ToInt16(packetData, 23);
		short target3Distance = BitConverter.ToInt16(packetData, 25);
		short target3Speed = BitConverter.ToInt16(packetData, 27);
		string line = $"目标1距离：{target1Distance}，目标1速度：{target1Speed}，目标2距离：{target2Distance}， 目标2速度：{target2Speed}， 目标3距离：{target3Distance}， 目标3速度：{target3Speed}";
		((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
		{
			tbRadarData.Text = line + "\n" + tbRadarData.Text;
		});
	}

	private void ParseVideoPacket(byte[] packetData)
	{
		ushort num = BitConverter.ToUInt16(packetData, 15);
		int padSize = packetData[17];
		int numOfPackets = packetData[18];
		int packetIndex = packetData[19];
		int imageSize = num - 3 - padSize;
		byte[] imageBytes = new byte[imageSize];
		Array.Copy(packetData, 20, imageBytes, 0, imageSize);
		m_packetBuffer[packetIndex] = imageBytes;
		if (m_expectedPackets == -1)
		{
			m_expectedPackets = numOfPackets;
		}
		if (m_packetBuffer.Count == m_expectedPackets)
		{
			List<byte> completeImageData = new List<byte>();
			for (int i = 0; i < m_expectedPackets; i++)
			{
				completeImageData.AddRange(m_packetBuffer[i]);
			}
			m_packetBuffer.Clear();
			m_expectedPackets = -1;
			((DispatcherObject)this).Dispatcher.Invoke((Action)delegate
			{
				DisplayImage(recvVideoDisplay, completeImageData.ToArray());
			});
		}
	}

	private void OnUpdateRadarParam_Click(object sender, RoutedEventArgs e)
	{
		m_deviceStarted = true;
		m_mode = Settings.Default.Mode;
		m_commMode = Settings.Default.CommMode;
		m_radarSignal = Settings.Default.RadarSignal;
		m_radarBandWidth = Settings.Default.RadarBandWidth;
		m_radarTimeWidth = Settings.Default.RadarTimeWidth;
		m_Nd = Settings.Default.Nd;
		m_Nr = Settings.Default.Nr;
		m_Fc = Settings.Default.Fc;
		m_radarReturnMode = Settings.Default.RadarReturnDataMode;
		m_M = Settings.Default.M;
		m_N = Settings.Default.N;
		m_TxS1 = Settings.Default.TXS1;
		m_TxL1 = Settings.Default.TXL1;
		m_RxS2 = Settings.Default.RXS2;
		m_RxL2 = Settings.Default.RXL2;
		string gvalue = Settings.Default.G;
		if (!string.IsNullOrEmpty(gvalue))
		{
			m_G = (from s in gvalue.Split(',')
				select int.Parse(s.Trim())).ToList();
		}
		m_bctlvalues = BeamControlGenerator.GenerateBeamControlCommands(m_TxS1, m_TxL1, m_RxS2, m_RxL2, m_M, m_G);
		m_sendRadarCmd = true;
	}

	private async void OnClickSetting(object sender, RoutedEventArgs e)
	{
		byte previousCommMode = Settings.Default.CommMode;
		bool restartWaveform = m_isSending;
		SettingDialog settingDialog = new SettingDialog();
		settingDialog.Owner = this;
		settingDialog.ShowDialog();
		ApplyQpskTuningSettings();

		byte selectedCommMode = Settings.Default.CommMode;
		if (!restartWaveform || selectedCommMode == previousCommMode)
		{
			return;
		}

		OnStopStreaming_Click(this, new RoutedEventArgs());
		await Task.Delay(150);
		OnStartSending_Click(this, new RoutedEventArgs());
		tbStatus.Text = $"波形模式已切换: {WaveformGenerator.GetModeName(selectedCommMode)}";
	}

	[DebuggerNonUserCode]
	[GeneratedCode("PresentationBuildTasks", "9.0.11.0")]
	public void InitializeComponent()
	{
		if (!_contentLoaded)
		{
			_contentLoaded = true;
			Uri resourceLocater = new Uri("/remotevideo;component/mainwindow.xaml", UriKind.Relative);
			Application.LoadComponent(this, resourceLocater);
		}
	}

	[DebuggerNonUserCode]
	[GeneratedCode("PresentationBuildTasks", "9.0.11.0")]
	[EditorBrowsable(EditorBrowsableState.Never)]
	void IComponentConnector.Connect(int connectionId, object target)
	{
		switch (connectionId)
		{
		case 1:
			((MenuItem)target).Click += OnClickSetting;
			break;
		case 2:
			tbStatus = (TextBlock)target;
			break;
		case 3:
			tbListenPort = (TextBlock)target;
			break;
		case 4:
			tbVersion = (TextBlock)target;
			break;
		case 5:
			btnStartSending = (Button)target;
			btnStartSending.Click += OnStartSending_Click;
			break;
		case 6:
			btnStopSending = (Button)target;
			btnStopSending.Click += OnStopStreaming_Click;
			break;
		case 7:
			btnStartDevice = (Button)target;
			btnStartDevice.Click += OnUpdateRadarParam_Click;
			break;
		case 8:
			((Button)target).Click += OnClickSendRadarData;
			break;
		case 9:
			videoDisplay = (Image)target;
			break;
		case 10:
			recvVideoDisplay = (Image)target;
			break;
		case 11:
			tbRadarData = (TextBox)target;
			break;
		default:
			_contentLoaded = true;
			break;
		}
	}
}
