using System;
using System.CodeDom.Compiler;
using System.ComponentModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
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
		txtFilePath.Text = string.Empty;
		txtRemoteIP.Text = Settings.Default.RemoteEndAddr;
		txtRemotePort.Text = Settings.Default.RemoteEndPort.ToString();
		txtLocalPort.Text = Settings.Default.LocalRecvPort.ToString();
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
		Settings.Default.VideoFilePath = IsVideoTransmissionMode(commMode) ? txtFilePath.Text.Trim() : string.Empty;
		Settings.Default.RemoteEndAddr = txtRemoteIP.Text;
		Settings.Default.RemoteEndPort = int.Parse(txtRemotePort.Text);
		Settings.Default.LocalRecvPort = int.Parse(txtLocalPort.Text);
		Settings.Default.Save();
		base.DialogResult = true;
	}

	private static bool IsVideoTransmissionMode(byte commMode)
	{
		return commMode == 8 || commMode == 9;
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
