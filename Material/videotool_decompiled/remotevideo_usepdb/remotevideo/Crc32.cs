using System;

namespace remotevideo;

internal static class Crc32
{
	private static readonly uint[] Table = BuildTable();

	public static uint Compute(ReadOnlySpan<byte> data)
	{
		uint crc = 0xFFFFFFFFu;
		foreach (byte value in data)
		{
			crc = Table[(crc ^ value) & 0xFF] ^ (crc >> 8);
		}
		return ~crc;
	}

	private static uint[] BuildTable()
	{
		uint[] table = new uint[256];
		for (uint i = 0; i < table.Length; i++)
		{
			uint value = i;
			for (int bit = 0; bit < 8; bit++)
			{
				value = (value & 1) != 0
					? 0xEDB88320u ^ (value >> 1)
					: value >> 1;
			}
			table[i] = value;
		}
		return table;
	}
}
