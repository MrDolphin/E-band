using System;
using System.Collections.Generic;

public class BeamControlGenerator
{
	public static byte[] GenerateBeamControlCommands(int s1, int l1, int s2, int l2, int M, List<int> azimuthArray)
	{
		if (azimuthArray == null || azimuthArray.Count == 0)
		{
			throw new ArgumentException("方位数组不能为空");
		}
		int N = azimuthArray.Count;
		List<byte> result = new List<byte>(2 + 2 * N * 11);
		result.Add((byte)M);
		result.Add((byte)N);
		for (int i = 0; i < N; i++)
		{
			int gValue = azimuthArray[i];
			int txStartRow = s1 + l1 * (gValue - 1);
			int rxStartRow = s2 + l2 * (gValue - 1);
			GenerateSingleCommand(result, txStartRow, l1);
			GenerateSingleCommand(result, rxStartRow, l2);
		}
		return result.ToArray();
	}

	private static void GenerateSingleCommand(List<byte> result, int startRow, int rowCount)
	{
		List<byte> payloadBytes = new List<byte>();
		byte header1 = 165;
		byte header2 = 90;
		byte payloadLength = 7;
		payloadBytes.Add(payloadLength);
		byte command = 48;
		payloadBytes.Add(command);
		byte[] startRowBytes = IntToBigEndian3Bytes(startRow);
		payloadBytes.AddRange(startRowBytes);
		byte[] rowCountBytes = IntToBigEndian3Bytes(rowCount);
		payloadBytes.AddRange(rowCountBytes);
		byte checksum = CalculateChecksum(payloadBytes);
		result.Add(header1);
		result.Add(header2);
		result.AddRange(payloadBytes);
		result.Add(checksum);
	}

	private static byte CalculateChecksum(List<byte> payloadBytes)
	{
		int sum = 0;
		foreach (byte b in payloadBytes)
		{
			sum += b;
		}
		return (byte)(sum & 0xFF);
	}

	private static byte[] IntToBigEndian3Bytes(int value)
	{
		if (value < 0 || value > 16777215)
		{
			throw new ArgumentOutOfRangeException("value", "值必须在0到16,777,215之间");
		}
		return new byte[3]
		{
			(byte)((value >> 16) & 0xFF),
			(byte)((value >> 8) & 0xFF),
			(byte)(value & 0xFF)
		};
	}
}
