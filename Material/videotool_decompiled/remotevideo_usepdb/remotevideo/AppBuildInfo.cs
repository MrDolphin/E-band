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
		string fromGit = TryReadGitCommit(AppContext.BaseDirectory);
		if (!string.IsNullOrWhiteSpace(fromGit))
		{
			return fromGit;
		}

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

	private static string TryReadGitCommit(string startDirectory)
	{
		try
		{
			DirectoryInfo directory = new DirectoryInfo(startDirectory);
			while (directory != null)
			{
				string gitPath = Path.Combine(directory.FullName, ".git");
				if (Directory.Exists(gitPath))
				{
					return ReadGitHead(gitPath, directory.FullName);
				}
				if (File.Exists(gitPath))
				{
					string content = File.ReadAllText(gitPath).Trim();
					const string gitDirPrefix = "gitdir:";
					if (content.StartsWith(gitDirPrefix, StringComparison.OrdinalIgnoreCase))
					{
						string gitDir = content[gitDirPrefix.Length..].Trim();
						if (!Path.IsPathRooted(gitDir))
						{
							gitDir = Path.GetFullPath(Path.Combine(directory.FullName, gitDir));
						}
						return ReadGitHead(gitDir, directory.FullName);
					}
				}
				directory = directory.Parent;
			}
		}
		catch (IOException ex)
		{
			Debug.WriteLine(ex.Message);
		}
		catch (UnauthorizedAccessException ex)
		{
			Debug.WriteLine(ex.Message);
		}
		return null;
	}

	private static string ReadGitHead(string gitDirectory, string workTreeDirectory)
	{
		string headPath = Path.Combine(gitDirectory, "HEAD");
		if (!File.Exists(headPath))
		{
			return null;
		}

		string head = File.ReadAllText(headPath).Trim();
		const string refPrefix = "ref:";
		if (!head.StartsWith(refPrefix, StringComparison.OrdinalIgnoreCase))
		{
			return ShortCommit(head);
		}

		string reference = head[refPrefix.Length..].Trim().Replace('/', Path.DirectorySeparatorChar);
		string refPath = Path.Combine(gitDirectory, reference);
		if (File.Exists(refPath))
		{
			return ShortCommit(File.ReadAllText(refPath).Trim());
		}

		string packedRefsPath = Path.Combine(gitDirectory, "packed-refs");
		if (!File.Exists(packedRefsPath))
		{
			return null;
		}
		string unixReference = reference.Replace(Path.DirectorySeparatorChar, '/');
		foreach (string line in File.ReadLines(packedRefsPath))
		{
			if (line.Length > 41 && line.EndsWith(unixReference, StringComparison.Ordinal))
			{
				return ShortCommit(line[..40]);
			}
		}
		return null;
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
