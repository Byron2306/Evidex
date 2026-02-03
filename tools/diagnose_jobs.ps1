param(
  [string]$Root = 'C:\Users\User\Desktop\EvidenceEngine\EvidenceEngine'
)

if(!(Test-Path $Root)){
  Write-Host "Root not found: $Root"
  exit 1
}

$stages=@('incoming','processing','done','failed')
Write-Host '=== STAGE COUNTS ==='
foreach($s in $stages){
  $p=Join-Path $Root $s
  $n=0
  if(Test-Path $p){ $n=(Get-ChildItem -LiteralPath $p -Directory -Force -ErrorAction SilentlyContinue | Measure-Object).Count }
  Write-Host ("{0,-10} {1,3}  {2}" -f $s,$n,$p)
}

function FirstLine([string]$Path){
  try {
    $raw=(Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue)
    if(!$raw){ return '' }
    return ($raw -split "`r?`n" | Select-Object -First 1)
  } catch { return '' }
}

Write-Host "\n=== PROCESSING JOBS (why blocked) ==="
$proc=Join-Path $Root 'processing'
if(Test-Path $proc){
  Get-ChildItem -LiteralPath $proc -Directory -Force | Sort-Object Name | ForEach-Object {
    $j=$_.FullName; $name=$_.Name
    $retry=Join-Path $j 'RETRY_LATER.txt'
    $err=Join-Path $j 'ERROR.txt'
    $need=Join-Path $j 'UPLOADS_ACTION_REQUIRED.txt'
    $zip=Join-Path $j 'DELIVERABLE.zip'
    $contact=Join-Path $j 'CONTACT_EMAIL.txt'

    $uploads=Join-Path $j 'uploads'
    $uploadCount=0
    if(Test-Path $uploads){
      $uploadCount=(Get-ChildItem -LiteralPath $uploads -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin @('desktop.ini','thumbs.db','.keep','.gitkeep','readme.txt') } |
        Measure-Object).Count
    }

    $zipMb=''
    if(Test-Path $zip){ $zipMb=[Math]::Round(((Get-Item $zip).Length/1MB),1) }

    $reason=@()
    if(Test-Path $need){ $reason += 'uploads_action_required' }
    if(Test-Path $err){ $reason += 'error' }
    if(Test-Path $retry){ $reason += 'retry_later' }
    if(!(Test-Path $contact)){ $reason += 'missing_contact' }
    if(!(Test-Path $zip)){ $reason += 'missing_zip' }

    Write-Host ("- {0}  uploads={1}  zipMB={2}  reason=[{3}]" -f $name,$uploadCount,$zipMb,($reason -join ', '))

    if(Test-Path $retry){ Write-Host ('  RETRY_LATER: ' + (FirstLine $retry)) }
    if(Test-Path $err){ Write-Host ('  ERROR: ' + (FirstLine $err)) }
    if(Test-Path $need){ Write-Host ('  UPLOADS_ACTION_REQUIRED: ' + (FirstLine $need)) }
  }
}

Write-Host "\n=== FAILED JOBS (first error line) ==="
$failed=Join-Path $Root 'failed'
if(Test-Path $failed){
  Get-ChildItem -LiteralPath $failed -Directory -Force | Sort-Object Name | ForEach-Object {
    $j=$_.FullName; $name=$_.Name
    $err=Join-Path $j 'ERROR.txt'
    $retry=Join-Path $j 'RETRY_LATER.txt'
    Write-Host "- $name"
    if(Test-Path $err){ Write-Host ('  ERROR: ' + (FirstLine $err)) }
    if(Test-Path $retry){ Write-Host ('  RETRY_LATER: ' + (FirstLine $retry)) }
  }
}

Write-Host "\n=== DONE JOBS (delivery readiness) ==="
$done=Join-Path $Root 'done'
if(Test-Path $done){
  Get-ChildItem -LiteralPath $done -Directory -Force | Sort-Object Name | ForEach-Object {
    $j=$_.FullName; $name=$_.Name
    $zip=Test-Path (Join-Path $j 'DELIVERABLE.zip')
    $contact=Test-Path (Join-Path $j 'CONTACT_EMAIL.txt')
    $sent=Test-Path (Join-Path $j 'SENT.txt')
    $statusPath=Join-Path $j 'DELIVERY_STATUS.txt'
    $status=''
    if(Test-Path $statusPath){ $status=(FirstLine $statusPath) }
    Write-Host "- $name  zip=$zip contact=$contact sent=$sent  $status"
  }
}
