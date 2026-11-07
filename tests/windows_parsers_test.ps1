# Windows parity test: the PowerShell window's file-count and output parsers against the same cases as
# tests/test_core.py (Python). Run: pwsh -File tests\windows_parsers_test.ps1   (or powershell -File ...)
$src = Join-Path $PSScriptRoot "..\scripts\progress_window.ps1"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$null, [ref]$null)
$ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
  $args[0].Name -in "Get-PatternRegex", "Get-FilesState", "Get-OutputProgress", "Read-Tail", "Test-Held", "Get-State" }, $true) |
  ForEach-Object { . ([scriptblock]::Create($_.Extent.Text)) }

$fails = 0
function Check($name, $got, $want) {
  $g = ($got | ForEach-Object { "$_" }) -join "|"; $w = ($want | ForEach-Object { "$_" }) -join "|"
  if ($g -eq $w) { "ok   $name" } else { "FAIL $name   got [$g], want [$w]"; $script:fails++ }
}

$dir = Join-Path ([IO.Path]::GetTempPath()) "cpb_parsers_test"; Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $dir | Out-Null
1..232 | ForEach-Object { New-Item -ItemType File (Join-Path $dir ("f_{0:D4}.png" -f $_)) | Out-Null }
"x" | Set-Content (Join-Path $dir "notes.txt"); New-Item -ItemType File (Join-Path $dir "f_0300.jpg") | Out-Null
$job = [pscustomobject]@{ kind = "files"; log = $dir; pattern = "f_####.png"; first = 193; last = 240 }
Check "files: 40 of 193-240 present" (Get-State $job $null) @(83, "40/48 frames", "run")
233..240 | ForEach-Object { New-Item -ItemType File (Join-Path $dir ("f_{0:D4}.png" -f $_)) | Out-Null }
Check "files: all present is done" (Get-State $job $null) @(100, "48/48 frames", "done")
Check "files: missing folder" (Get-State ([pscustomobject]@{ kind = "files"; log = "$dir\nope"; pattern = "f_####.png"; first = 193; last = 240 }) $null) @(0, "0/48 frames", "run")
New-Item -ItemType File (Join-Path $dir "SHOT_007.PNG") | Out-Null
Check "files: case-insensitive, own unit" (Get-FilesState ([pscustomobject]@{ log = $dir; pattern = "shot_###.png"; first = 1; last = 10; unit = "images" })) @(10, "1/10 images", "run")

Check "output: percent" (Get-OutputProgress "loading`nRendering 42%`n") @(42, "Rendering 42%")
Check "output: N/M" (Get-OutputProgress "frame 12/48 done`n") @(25, "12/48")
Check "output: N of M" (Get-OutputProgress "item 3 of 4`n") @(75, "3/4")
Check "output: Blender Fra:" (Get-OutputProgress "Fra:193 Mem:512M | Rendering 3/40 samples`nFra:195 Mem:512M`n") @(-1, "frame 195")
Check "output: a date is not progress" (Get-OutputProgress "saved 11/07/2026`n") @()
Check "output: a 01/07 date is not progress" (Get-OutputProgress "run on 01/07/2026`n") @()
Check "output: nothing" (Get-OutputProgress "hello`n") @()

Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
""; if ($fails) { "$fails failed"; exit 1 } else { "all passed" }
