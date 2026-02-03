param(
  [string]$Root = 'C:\Users\User\Desktop\EvidenceEngine\EvidenceEngine',
  [switch]$Apply,
  [switch]$ForceDelete,
  [bool]$IncludeIncomingIncomplete = $true,
  [bool]$CleanBrokenDone = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DryRun = -not $Apply

function Write-Section($t){ Write-Host "\n=== $t ===" }

function Test-ExistsDir([string]$p){ return (Test-Path -LiteralPath $p) -and (Get-Item -LiteralPath $p -ErrorAction SilentlyContinue).PSIsContainer }

function Get-StageDirs {
  param([string]$Root)
  $stages=@('incoming','processing','done','failed')
  $out=@()
  foreach($s in $stages){
    $p=Join-Path $Root $s
    if(Test-ExistsDir $p){
      $out += [pscustomobject]@{ Stage=$s; Path=$p }
    }
  }
  return $out
}

function Normalize-BaseName {
  param([string]$Name)
  $n = $Name
  # strip trailing " (1)"
  $n = [regex]::Replace($n, "\s*\(\d+\)\s*$", "")
  # strip common suffixes like __retry_123, __done_123, __reset_123
  $n = [regex]::Replace($n, "__retry_\d+$", "")
  $n = [regex]::Replace($n, "__done_\d+$", "")
  $n = [regex]::Replace($n, "__reset_\d+$", "")
  # strip trailing timestamp-ish suffixes: __1769100586078
  $n = [regex]::Replace($n, "__\d{10,}$", "")
  return $n
}

function Count-Uploads {
  param([string]$JobDir)
  $uploads = Join-Path $JobDir 'uploads'
  if(!(Test-ExistsDir $uploads)){ return 0 }
  $skip=@('desktop.ini','thumbs.db','.keep','.gitkeep','readme.txt')
  $files = Get-ChildItem -LiteralPath $uploads -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $skip -notcontains $_.Name.ToLowerInvariant() }
  return ($files | Measure-Object).Count
}

function Get-JobScore {
  param([string]$Stage,[string]$JobDir)
  $score = 0
  $hasZip = Test-Path -LiteralPath (Join-Path $JobDir 'DELIVERABLE.zip')
  $hasContact = Test-Path -LiteralPath (Join-Path $JobDir 'CONTACT_EMAIL.txt')
  $hasSent = Test-Path -LiteralPath (Join-Path $JobDir 'SENT.txt')
  if($hasZip){ $score += 100 }
  if($hasContact){ $score += 20 }
  if($hasSent){ $score += 10 }
  switch($Stage){
    'done' { $score += 8 }
    'processing' { $score += 5 }
    'incoming' { $score += 3 }
    'failed' { $score += 0 }
  }
  # minor: prefer jobs with more uploads
  $score += [Math]::Min(10, (Count-Uploads $JobDir))
  # minor: prefer newer
  try {
    $t = (Get-Item -LiteralPath $JobDir).LastWriteTimeUtc
    $score += [int]([Math]::Min(10, (([DateTime]::UtcNow - $t).TotalHours * -0.1)))
  } catch {}
  return $score
}

function Is-IncompleteTestJob {
  param([string]$Stage,[string]$JobDir)
  $intake = Join-Path $JobDir 'intake.yaml'
  $uploads = Join-Path $JobDir 'uploads'
  $needUploads = Test-Path -LiteralPath (Join-Path $JobDir 'UPLOADS_ACTION_REQUIRED.txt')
  if($needUploads){ return $true }

  $hasIntake = Test-Path -LiteralPath $intake
  $hasUploadsDir = Test-ExistsDir $uploads
  if(!$hasIntake -or !$hasUploadsDir){
    # Only treat incoming as deletable if user opts in.
    if($Stage -eq 'incoming' -and !$IncludeIncomingIncomplete){ return $false }
    return $true
  }

  # empty uploads = test/incomplete
  if((Count-Uploads $JobDir) -le 0){
    if($Stage -eq 'incoming' -and !$IncludeIncomingIncomplete){ return $false }
    return $true
  }

  # tiny intake = likely broken metadata
  try {
    $sz = (Get-Item -LiteralPath $intake).Length
    if($sz -lt 30){
      if($Stage -eq 'incoming' -and !$IncludeIncomingIncomplete){ return $false }
      return $true
    }
  } catch {}

  return $false
}

function Ensure-Dir([string]$p){ if(!(Test-Path -LiteralPath $p)){ New-Item -ItemType Directory -Force -Path $p | Out-Null } }

function Retry {
  param([scriptblock]$Fn,[int]$Attempts=8,[int]$DelayMs=400)
  for($i=0;$i -lt $Attempts;$i++){
    try { & $Fn; return } catch {
      if($i -ge ($Attempts-1)){ throw }
      Start-Sleep -Milliseconds ($DelayMs * [Math]::Pow(2,$i))
    }
  }
}

function Trash-Or-Delete {
  param(
    [string]$Stage,
    [string]$JobDir,
    [string]$Reason,
    [string]$TrashRoot
  )

  $name = Split-Path -Leaf $JobDir
  if($DryRun){
    Write-Host "DRYRUN: $Reason -> $Stage\\$name"
    return
  }

  if($ForceDelete){
    Write-Host "DELETE: $Reason -> $Stage\\$name"
    try {
      Retry -Fn { Remove-Item -LiteralPath $JobDir -Recurse -Force -ErrorAction Stop }
    } catch {
      Write-Host "SKIP (delete failed, likely locked): $Stage\\$name  $($_.Exception.Message)"
    }
    return
  }

  Ensure-Dir (Join-Path $TrashRoot $Stage)
  $dst = Join-Path (Join-Path $TrashRoot $Stage) $name
  if(Test-Path -LiteralPath $dst){
    $dst = Join-Path (Join-Path $TrashRoot $Stage) ($name + '__' + [DateTime]::UtcNow.ToString('HHmmss'))
  }

  Write-Host "TRASH: $Reason -> $Stage\\$name"
  try {
    Retry -Fn { Move-Item -LiteralPath $JobDir -Destination $dst -Force -ErrorAction Stop }
  } catch {
    Write-Host "SKIP (move failed, likely locked): $Stage\\$name  $($_.Exception.Message)"
  }
}

# ---- Main ----
if(!(Test-ExistsDir $Root)){
  throw "Root folder not found: $Root"
}

Write-Section "Config"
Write-Host "Root: $Root"
Write-Host "Apply: $Apply"
Write-Host "DryRun: $DryRun"
Write-Host "ForceDelete: $ForceDelete"
Write-Host "IncludeIncomingIncomplete: $IncludeIncomingIncomplete"
Write-Host "CleanBrokenDone: $CleanBrokenDone"

$trashRoot = Join-Path $Root (Join-Path '_trash' ([DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss')))

# Build job inventory
$jobs=@()
foreach($sd in (Get-StageDirs -Root $Root)){
  $stage=$sd.Stage
  Get-ChildItem -LiteralPath $sd.Path -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $dir=$_.FullName
    $jobs += [pscustomobject]@{
      Stage=$stage
      Name=$_.Name
      Base=(Normalize-BaseName $_.Name)
      Path=$dir
      Score=(Get-JobScore -Stage $stage -JobDir $dir)
      HasZip=(Test-Path -LiteralPath (Join-Path $dir 'DELIVERABLE.zip'))
      HasContact=(Test-Path -LiteralPath (Join-Path $dir 'CONTACT_EMAIL.txt'))
      HasSent=(Test-Path -LiteralPath (Join-Path $dir 'SENT.txt'))
      Uploads=(Count-Uploads $dir)
      IsTestIncomplete=(Is-IncompleteTestJob -Stage $stage -JobDir $dir)
    }
  }
}

Write-Section "Stage counts"
$jobs | Group-Object Stage | Sort-Object Name | ForEach-Object { Write-Host ("{0,-10} {1,3}" -f $_.Name, $_.Count) }

# 1) Delete failed/incomplete tests
Write-Section "Incomplete test jobs (candidate delete)"
$toDelete = @()
$toDelete += $jobs | Where-Object { $_.Stage -eq 'failed' -and $_.IsTestIncomplete }
if($IncludeIncomingIncomplete){
  $toDelete += $jobs | Where-Object { $_.Stage -eq 'incoming' -and $_.IsTestIncomplete }
}
$toDelete = $toDelete | Sort-Object Stage, Name -Unique

if($toDelete.Count -eq 0){
  Write-Host "(none)"
} else {
  foreach($j in $toDelete){
    Trash-Or-Delete -Stage $j.Stage -JobDir $j.Path -Reason 'incomplete_test' -TrashRoot $trashRoot
  }
}

# 2) Duplicates: keep best per Base, remove others
Write-Section "Duplicate jobs (keep best per base name)"
$dupeGroups = $jobs | Group-Object Base | Where-Object { $_.Count -gt 1 }
if($dupeGroups.Count -eq 0){
  Write-Host "(none)"
} else {
  foreach($g in $dupeGroups){
    $items = $g.Group | Sort-Object Score -Descending
    $keep = $items[0]
    Write-Host "Base: $($g.Name)  keep: $($keep.Stage)\\$($keep.Name) score=$($keep.Score)"

    foreach($j in $items | Select-Object -Skip 1){
      # Do not delete already-sent jobs.
      if($j.HasSent){ continue }
      Trash-Or-Delete -Stage $j.Stage -JobDir $j.Path -Reason ('duplicate_of_' + $keep.Name) -TrashRoot $trashRoot
    }
  }
}

# 3) Optionally: broken done folders with no zip + no contact
if($CleanBrokenDone){
  Write-Section "Broken done/ folders (no zip + no contact)"
  $broken = $jobs | Where-Object { $_.Stage -eq 'done' -and (-not $_.HasZip) -and (-not $_.HasContact) -and (-not $_.HasSent) }
  if($broken.Count -eq 0){
    Write-Host "(none)"
  } else {
    foreach($j in ($broken | Sort-Object Name)){
      Trash-Or-Delete -Stage $j.Stage -JobDir $j.Path -Reason 'broken_done_missing_zip_and_contact' -TrashRoot $trashRoot
    }
  }
}

Write-Section "Done"
if($DryRun){
  Write-Host "Dry-run complete. Re-run with -DryRun:$false to apply."
  Write-Host "To permanently delete instead of trash, add -ForceDelete."
} else {
  if($ForceDelete){
    Write-Host "Cleanup applied (PERMANENT delete)."
  } else {
    Write-Host "Cleanup applied. Moved items to: $trashRoot"
  }
}
