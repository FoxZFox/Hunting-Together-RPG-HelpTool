# Windows built-in OCR server. Runs under Windows PowerShell 5.1 (powershell.exe), not pwsh.
# Reads one image path per line on stdin, writes one JSON line per image on stdout:
#   {"lines":[{"text":"Level 33 · 43.5%","x":8,"y":690,"w":80,"h":12}, ...]}
# Coordinates are in the image's own pixels.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function Await($op, [Type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $task.Wait()
  $task.Result
}

$lang = New-Object Windows.Globalization.Language 'en-US'
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }

while ($null -ne ($path = [Console]::In.ReadLine())) {
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()
    $lines = @(foreach ($line in $result.Lines) {
      $rects = @($line.Words | ForEach-Object { $_.BoundingRect })
      $x1 = ($rects | Measure-Object -Property X -Minimum).Minimum
      $y1 = ($rects | Measure-Object -Property Y -Minimum).Minimum
      $x2 = ($rects | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
      $y2 = ($rects | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
      [ordered]@{ text = $line.Text; x = [int]$x1; y = [int]$y1; w = [int]($x2 - $x1); h = [int]($y2 - $y1) }
    })
    [Console]::Out.WriteLine((ConvertTo-Json @{ lines = $lines } -Compress -Depth 4))
  } catch {
    $err = $_.Exception
    while ($err.InnerException) { $err = $err.InnerException }
    [Console]::Out.WriteLine((ConvertTo-Json @{ lines = @(); error = $err.Message } -Compress))
  }
  [Console]::Out.Flush()
}
