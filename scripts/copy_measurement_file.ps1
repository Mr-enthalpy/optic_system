# Copy one closed file from a local/SMB path using a trusted sender SHA-256.
# Data eligibility is checked separately with inspect_raw_capture --require-success.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedSha256,
    [ValidateRange(1, 10)][int]$Attempts = 3,
    [ValidateRange(0, 60)][int]$RetrySeconds = 2
)
$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $Source).ProviderPath
$destinationPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
if ($sourcePath -eq $destinationPath) { throw 'Source and destination must be different files' }
if (Test-Path -LiteralPath $Destination -PathType Container) { throw 'Destination must be a file path' }

for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    $sourceStream = $targetStream = $null
    try {
        # Sharing permits readers, but denies a concurrent writer on Windows/SMB.
        $sourceStream = [IO.File]::Open($sourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $sourceHash = (Get-FileHash -InputStream $sourceStream -Algorithm SHA256).Hash
        if ($sourceHash -ne $ExpectedSha256) { throw 'Source SHA-256 does not match trusted sender hash' }
        $sourceStream.Position = 0
        if (Test-Path -LiteralPath $destinationPath) {
            if ((Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
                throw 'Existing destination SHA-256 mismatch; refusing to overwrite it'
            }
            Write-Output "Verified existing file: $destinationPath"
            return
        }
        $parentPath = [IO.Path]::GetDirectoryName($destinationPath)
        [IO.Directory]::CreateDirectory($parentPath) | Out-Null
        # Failed attempts remain identifiable and cannot become formal inputs.
        $partialPath = $destinationPath + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
        $targetStream = [IO.File]::Open($partialPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $sourceStream.CopyTo($targetStream)
        $targetStream.Flush($true)
        $targetStream.Dispose()
        $targetStream = $null
        if ((Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
            throw "Copied file SHA-256 mismatch; retain partial for diagnosis: $partialPath"
        }
        # Move within the destination directory; never replace an existing file.
        [IO.File]::Move($partialPath, $destinationPath)
        Write-Output "Verified copied file: $destinationPath"
        return
    }
    catch {
        if ($attempt -eq $Attempts) { throw }
        Write-Warning "Copy attempt $attempt failed: $($_.Exception.Message)"
        if ($RetrySeconds -gt 0) { Start-Sleep -Seconds $RetrySeconds }
    }
    finally {
        if ($null -ne $targetStream) { $targetStream.Dispose() }
        if ($null -ne $sourceStream) { $sourceStream.Dispose() }
    }
}
