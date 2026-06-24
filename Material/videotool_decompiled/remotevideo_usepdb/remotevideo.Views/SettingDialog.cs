using System;
using System.CodeDom.Compiler;
using System.ComponentModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using Microsoft.Win32;
using remotevideo.Properties;

namespace remotevideo.Views;

public class SettingDialog : Window, IComponentConnector
{
	internal Button btnDialogOk;

	internal ComboBox tbxMode;

	internal TextBox tbScanCount;

	internal TextBox tbTxS1;

	internal TextBox tbTxL1;

	internal TextBox tbRxS2;

	internal TextBox tbRxL2;

	internal TextBox tbN;

	internal TextBox tbG;

	internal TextBox tbRadarSignal;

	internal TextBox tbRadarBandWidth;

	internal TextBox tbRadarTimeWidth;

	internal TextBox tbRadarFc;

	internal TextBox tbRadarNd;

	internal TextBox tbRadarNr;

	internal TextBox txtFilePath;

	internal Button btnBrowse;

	internal TextBox tbCommMode;

	internal TextBox txtRemoteIP;

	internal TextBox txtRemotePort;

	internal TextBox txtLocalPort;

	private TextBox tbVideoRxPeakThreshold;

	private TextBox tbVideoActiveRxHangoverFrames;

	private TextBox tbVideoDecodeBatchFrames;

	private TextBox tbVideoDecodeQueueLimit;

	private TextBox tbVideoRepairRoundCount;

	private TextBox tbVideoRepairWaitMs;

	private TextBox tbQpskTxScalePercent;

	private TextBox tbQpskPreambleThresholdPercent;

	private TextBox tbQpskQuickThresholdPercent;

	private TextBox tbQpskPreambleSearchStep;

	private bool _contentLoaded;

	public SettingDialog()
	{
		InitializeComponent();
		tbxMode.SelectedIndex = Settings.Default.Mode;
		tbScanCount.Text = Settings.Default.M.ToString();
		tbTxS1.Text = Settings.Default.TXS1.ToString();
		tbTxL1.Text = Settings.Default.TXL1.ToString();
		tbRxS2.Text = Settings.Default.RXS2.ToString();
		tbRxL2.Text = Settings.Default.RXL2.ToString();
		tbN.Text = Settings.Default.N.ToString();
		tbG.Text = Settings.Default.G;
		tbRadarSignal.Text = Settings.Default.RadarSignal.ToString();
		tbRadarBandWidth.Text = Settings.Default.RadarBandWidth.ToString();
		tbRadarTimeWidth.Text = Settings.Default.RadarTimeWidth.ToString();
		tbRadarFc.Text = Settings.Default.Fc.ToString();
		tbRadarNd.Text = Settings.Default.Nd.ToString();
		tbRadarNr.Text = Settings.Default.Nr.ToString();
		tbCommMode.Text = Settings.Default.CommMode.ToString();
		UpdateVideoFileInputState();
		tbCommMode.TextChanged += OnCommModeTextChanged;
		txtRemoteIP.Text = Settings.Default.RemoteEndAddr;
		txtRemotePort.Text = Settings.Default.RemoteEndPort.ToString();
		txtLocalPort.Text = Settings.Default.LocalRecvPort.ToString();
		AddVideoDebugSettingsPanel();
	}

	private void BtnDialogOk_Click(object sender, RoutedEventArgs e)
	{
		Settings.Default.Mode = (byte)((tbxMode.SelectedIndex == 0) ? 1 : 2);
		Settings.Default.M = short.Parse(tbScanCount.Text);
		Settings.Default.TXS1 = int.Parse(tbTxS1.Text);
		Settings.Default.TXL1 = int.Parse(tbTxL1.Text);
		Settings.Default.RXS2 = int.Parse(tbRxS2.Text);
		Settings.Default.RXL2 = int.Parse(tbRxL2.Text);
		Settings.Default.N = short.Parse(tbN.Text);
		Settings.Default.G = tbG.Text;
		Settings.Default.RadarSignal = (byte)int.Parse(tbRadarSignal.Text);
		Settings.Default.RadarBandWidth = (byte)int.Parse(tbRadarBandWidth.Text);
		Settings.Default.RadarTimeWidth = (byte)int.Parse(tbRadarTimeWidth.Text);
		Settings.Default.Fc = (byte)int.Parse(tbRadarFc.Text);
		Settings.Default.Nd = (byte)int.Parse(tbRadarNd.Text);
		Settings.Default.Nr = (byte)int.Parse(tbRadarNr.Text);
		byte commMode = (byte)int.Parse(tbCommMode.Text);
		Settings.Default.CommMode = commMode;
		if (IsVideoTransmissionMode(commMode))
		{
			Settings.Default.VideoFilePath = txtFilePath.Text.Trim();
		}
		Settings.Default.RemoteEndAddr = txtRemoteIP.Text;
		Settings.Default.RemoteEndPort = int.Parse(txtRemotePort.Text);
		Settings.Default.LocalRecvPort = int.Parse(txtLocalPort.Text);
		Settings.Default.VideoRxPeakThreshold = ParseBoundedInt(
			tbVideoRxPeakThreshold,
			600,
			1,
			32767);
		Settings.Default.VideoActiveRxHangoverFrames = ParseBoundedInt(
			tbVideoActiveRxHangoverFrames,
			3,
			0,
			64);
		Settings.Default.VideoDecodeBatchFrames = ParseBoundedInt(
			tbVideoDecodeBatchFrames,
			32,
			1,
			256);
		Settings.Default.VideoDecodeQueueLimit = ParseBoundedInt(
			tbVideoDecodeQueueLimit,
			4096,
			256,
			32768);
		Settings.Default.VideoRepairRoundCount = ParseBoundedInt(
			tbVideoRepairRoundCount,
			4,
			0,
			20);
		Settings.Default.VideoRepairWaitMs = ParseBoundedInt(
			tbVideoRepairWaitMs,
			100,
			0,
			2000);
		Settings.Default.QpskTxScalePercent = ParseBoundedInt(
			tbQpskTxScalePercent,
			65,
			5,
			100);
		Settings.Default.QpskPreambleThresholdPercent = ParseBoundedInt(
			tbQpskPreambleThresholdPercent,
			65,
			25,
			95);
		Settings.Default.QpskQuickThresholdPercent = ParseBoundedInt(
			tbQpskQuickThresholdPercent,
			53,
			10,
			90);
		Settings.Default.QpskPreambleSearchStep = ParseBoundedInt(
			tbQpskPreambleSearchStep,
			4,
			1,
			4);
		Settings.Default.Save();
		base.DialogResult = true;
	}

	private static int ParseBoundedInt(TextBox textBox, int fallback, int min, int max)
	{
		if (textBox == null || !int.TryParse(textBox.Text.Trim(), out int value))
		{
			return fallback;
		}
		return Math.Max(min, Math.Min(max, value));
	}

	private static bool IsVideoTransmissionMode(byte commMode)
	{
		return commMode == 8 || commMode == 9;
	}

	private void OnCommModeTextChanged(object sender, TextChangedEventArgs e)
	{
		UpdateVideoFileInputState();
	}

	private void UpdateVideoFileInputState()
	{
		bool isVideoMode = byte.TryParse(tbCommMode.Text.Trim(), out byte commMode) && IsVideoTransmissionMode(commMode);
		txtFilePath.IsEnabled = isVideoMode;
		btnBrowse.IsEnabled = isVideoMode;
		txtFilePath.Text = isVideoMode ? Settings.Default.VideoFilePath : string.Empty;
	}

	private void OnBrowseFile_Click(object sender, RoutedEventArgs e)
	{
		OpenFileDialog openFileDialog = new OpenFileDialog
		{
			Filter = "Media Files|*.mp4;*.jpg;*.jpeg;*.png;*.bmp;*.webp|MP4 Files|*.mp4|Image Files|*.jpg;*.jpeg;*.png;*.bmp;*.webp|All Files|*.*"
		};
		if (openFileDialog.ShowDialog() == true)
		{
			txtFilePath.Text = openFileDialog.FileName;
		}
	}

	private void AddVideoDebugSettingsPanel()
	{
		tbVideoRxPeakThreshold = CreateTuningTextBox(
			Settings.Default.VideoRxPeakThreshold);
		tbVideoActiveRxHangoverFrames = CreateTuningTextBox(
			Settings.Default.VideoActiveRxHangoverFrames);
		tbVideoDecodeBatchFrames = CreateTuningTextBox(
			Settings.Default.VideoDecodeBatchFrames);
		tbVideoDecodeQueueLimit = CreateTuningTextBox(
			Settings.Default.VideoDecodeQueueLimit);
		tbVideoRepairRoundCount = CreateTuningTextBox(
			Settings.Default.VideoRepairRoundCount);
		tbVideoRepairWaitMs = CreateTuningTextBox(
			Settings.Default.VideoRepairWaitMs);
		tbQpskTxScalePercent = CreateTuningTextBox(
			Settings.Default.QpskTxScalePercent);
		tbQpskPreambleThresholdPercent = CreateTuningTextBox(
			Settings.Default.QpskPreambleThresholdPercent);
		tbQpskQuickThresholdPercent = CreateTuningTextBox(
			Settings.Default.QpskQuickThresholdPercent);
		tbQpskPreambleSearchStep = CreateTuningTextBox(
			Settings.Default.QpskPreambleSearchStep);

		Grid tuningGrid = new Grid
		{
			Margin = new Thickness(8, 4, 8, 4),
		};
		tuningGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
		tuningGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
		tuningGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
		tuningGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
		AddTuningCell(tuningGrid, 0, 0, "RX峰值门限", tbVideoRxPeakThreshold, "ADC 计数，低于此值的 RX IQ 不进入解调队列");
		AddTuningCell(tuningGrid, 0, 2, "门控拖尾帧", tbVideoActiveRxHangoverFrames, "检测到有效 burst 后额外保留的 RX 帧数");
		AddTuningCell(tuningGrid, 1, 0, "解调批帧数", tbVideoDecodeBatchFrames, "每次送入 QPSK 流解码器的 RX IQ 帧数");
		AddTuningCell(tuningGrid, 1, 2, "解调队列上限", tbVideoDecodeQueueLimit, "RX IQ 等待解调的最大队列长度");
		AddTuningCell(tuningGrid, 2, 0, "补发轮数", tbVideoRepairRoundCount, "每个视频/图片帧缺片后的选择性补发轮数");
		AddTuningCell(tuningGrid, 2, 2, "补发等待ms", tbVideoRepairWaitMs, "每轮补发前等待 RX 确认的时间");
		AddTuningCell(tuningGrid, 3, 0, "QPSK发送幅度%", tbQpskTxScalePercent, "发送 IQ 幅度百分比，过强或截断时调低");
		AddTuningCell(tuningGrid, 3, 2, "前导解调门限%", tbQpskPreambleThresholdPercent, "最终 QPSK 前导相关门限，65 表示 0.65");
		AddTuningCell(tuningGrid, 4, 0, "快速筛选门限%", tbQpskQuickThresholdPercent, "粗搜索快速前导筛选门限，53 表示 0.53");
		AddTuningCell(tuningGrid, 4, 2, "前导搜索步进", tbQpskPreambleSearchStep, "粗搜索采样步进，1 最细但更慢，4 为默认");

		GroupBox groupBox = new GroupBox
		{
			Header = "模式9调试参数（QPSK Video Tuning）",
			Content = tuningGrid,
			Margin = new Thickness(0, 10, 0, 0),
		};

		if (TryAppendBelowLocalPort(groupBox))
		{
			return;
		}
		if (Content is Panel panel)
		{
			panel.Children.Add(groupBox);
		}
	}

	private static TextBox CreateTuningTextBox(int value)
	{
		return new TextBox
		{
			Text = value.ToString(),
			MinWidth = 90,
			Margin = new Thickness(6, 2, 10, 2),
		};
	}

	private static void AddTuningCell(
		Grid grid,
		int row,
		int column,
		string label,
		TextBox textBox,
		string tooltip)
	{
		while (grid.RowDefinitions.Count <= row)
		{
			grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
		}
		Label labelControl = new Label
		{
			Content = label,
			ToolTip = tooltip,
			VerticalAlignment = VerticalAlignment.Center,
			Margin = new Thickness(0, 1, 4, 1),
		};
		textBox.ToolTip = tooltip;
		Grid.SetRow(labelControl, row);
		Grid.SetColumn(labelControl, column);
		Grid.SetRow(textBox, row);
		Grid.SetColumn(textBox, column + 1);
		grid.Children.Add(labelControl);
		grid.Children.Add(textBox);
	}

	private bool TryAppendBelowLocalPort(GroupBox groupBox)
	{
		Grid grid = FindVisualParent<Grid>(txtLocalPort);
		if (grid == null)
		{
			return false;
		}

		int row = grid.RowDefinitions.Count;
		grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
		Grid.SetRow(groupBox, row);
		Grid.SetColumn(groupBox, 0);
		Grid.SetColumnSpan(groupBox, Math.Max(1, grid.ColumnDefinitions.Count));
		grid.Children.Add(groupBox);
		return true;
	}

	private static T FindVisualParent<T>(DependencyObject child)
		where T : DependencyObject
	{
		DependencyObject current = child;
		while (current != null)
		{
			current = VisualTreeHelper.GetParent(current);
			if (current is T match)
			{
				return match;
			}
		}
		return null;
	}

	[DebuggerNonUserCode]
	[GeneratedCode("PresentationBuildTasks", "9.0.11.0")]
	public void InitializeComponent()
	{
		if (!_contentLoaded)
		{
			_contentLoaded = true;
			Uri resourceLocater = new Uri("/remotevideo;component/views/settingdialog.xaml", UriKind.Relative);
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
			btnDialogOk = (Button)target;
			btnDialogOk.Click += BtnDialogOk_Click;
			break;
		case 2:
			tbxMode = (ComboBox)target;
			break;
		case 3:
			tbScanCount = (TextBox)target;
			break;
		case 4:
			tbTxS1 = (TextBox)target;
			break;
		case 5:
			tbTxL1 = (TextBox)target;
			break;
		case 6:
			tbRxS2 = (TextBox)target;
			break;
		case 7:
			tbRxL2 = (TextBox)target;
			break;
		case 8:
			tbN = (TextBox)target;
			break;
		case 9:
			tbG = (TextBox)target;
			break;
		case 10:
			tbRadarSignal = (TextBox)target;
			break;
		case 11:
			tbRadarBandWidth = (TextBox)target;
			break;
		case 12:
			tbRadarTimeWidth = (TextBox)target;
			break;
		case 13:
			tbRadarFc = (TextBox)target;
			break;
		case 14:
			tbRadarNd = (TextBox)target;
			break;
		case 15:
			tbRadarNr = (TextBox)target;
			break;
		case 16:
			txtFilePath = (TextBox)target;
			break;
		case 17:
			btnBrowse = (Button)target;
			btnBrowse.Click += OnBrowseFile_Click;
			break;
		case 18:
			tbCommMode = (TextBox)target;
			break;
		case 19:
			txtRemoteIP = (TextBox)target;
			break;
		case 20:
			txtRemotePort = (TextBox)target;
			break;
		case 21:
			txtLocalPort = (TextBox)target;
			break;
		default:
			_contentLoaded = true;
			break;
		}
	}
}
