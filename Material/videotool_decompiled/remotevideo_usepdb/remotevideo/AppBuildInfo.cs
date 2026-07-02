using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

namespace remotevideo;

internal static class AppBuildInfo
{
	public const string AppName = "视频流测试工具";

	public static string BuildTitle(string appName, string version, string commitId)
	{
		string cleanVersion = string.IsNullOrWhiteSpace(version)
			? "unknown"
			: version.Trim();
		string cleanCommit = string.IsNullOrWhiteSpace(commitId)
			? "unknown"
			: ShortCommit(commitId.Trim());
		return $"{appName} v{cleanVersion} ({cleanCommit})";
	}

	public static string CurrentTitle(string version)
	{
		return BuildTitle(AppName, version, GetCommitId());
	}

	public static string GetCommitId()
	{
		string infoVersion =
			Assembly.GetExecutingAssembly()
				.GetCustomAttribute<AssemblyInformationalVersionAttribute>()
				?.InformationalVersion;
		int plusIndex = infoVersion?.IndexOf('+') ?? -1;
		if (plusIndex >= 0 && plusIndex + 1 < infoVersion.Length)
		{
			return ShortCommit(infoVersion[(plusIndex + 1)..]);
		}
		return "unknown";
	}

	private static string ShortCommit(string commitId)
	{
		if (string.IsNullOrWhiteSpace(commitId))
		{
			return "unknown";
		}
		return commitId.Length <= 7 ? commitId : commitId[..7];
	}
}
