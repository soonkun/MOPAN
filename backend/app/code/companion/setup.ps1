# MOPAN 코드 - 내 컴퓨터 연결(Windows PowerShell). 코드 탭의 "내 컴퓨터 연결"이 만들어 준 한 줄 명령이 이 파일을 받아 실행한다.
#   $env:MOPAN_SERVER='https://...'; $env:MOPAN_TOKEN='...'; irm https://.../api/code/setup.ps1 | iex
# 하는 일: 폴더 고르기 창 → Node.js 확인 → OpenCode 없으면 설치 → 컴패니언(mopan-code.mjs)을 %LOCALAPPDATA%\MOPAN 에
# 내려받기 → 다음부터 두 번 클릭할 connect.cmd 만들기 → 연결. 비밀은 이 파일에 없다(토큰은 환경 변수로 온다).
# 브라우저는 PC 폴더의 전체 경로를 알 수 없어서 폴더는 여기서 고른다($env:MOPAN_DIR를 주면 창 없이 그 폴더).
# 전체를 & { } 로 감싸고 exit 대신 return - iex 안의 exit는 붙여 넣은 PowerShell 창까지 닫아 안내문을 못 읽게 한다.
& {
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$server = $env:MOPAN_SERVER; $token = $env:MOPAN_TOKEN; $dir = $env:MOPAN_DIR
if (-not $server -or -not $token) {
  Write-Host "코드 탭 > 내 컴퓨터 연결에서 만든 명령을 그대로 붙여 넣어 주세요(MOPAN_SERVER, MOPAN_TOKEN이 필요합니다)."; return
}
if (-not $dir) {
  Write-Host "연결할 폴더를 고르는 창을 엽니다 ..."
  Add-Type -AssemblyName System.Windows.Forms
  $pick = New-Object System.Windows.Forms.FolderBrowserDialog
  $pick.Description = "MOPAN 에이전트가 읽고 쓸 폴더를 고르세요"
  $pick.ShowNewFolderButton = $true
  $owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }  # 창이 PowerShell 뒤에 숨지 않게
  $ok = $pick.ShowDialog($owner); $owner.Dispose()
  if ($ok -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host "폴더를 고르지 않아 멈췄습니다. 같은 명령을 다시 붙여 넣으면 됩니다."; return }
  $dir = $pick.SelectedPath
}
if (-not (Test-Path -LiteralPath $dir -PathType Container)) { Write-Host "폴더가 없습니다: $dir"; return }
$dir = (Resolve-Path -LiteralPath $dir).Path
$base = Join-Path $env:LOCALAPPDATA "MOPAN"
New-Item -ItemType Directory -Force -Path $base | Out-Null

# 1. Node.js 22+
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Write-Host ""
  Write-Host "Node.js가 없습니다. https://nodejs.org 에서 LTS(22 이상)를 설치한 뒤, PowerShell 창을 새로 열어 이 명령을 다시 붙여 넣어 주세요."
  Start-Process "https://nodejs.org/"
  return
}
$major = [int]((& node -v).TrimStart("v").Split(".")[0])
if ($major -lt 22) { Write-Host "Node.js $(& node -v)는 오래되었습니다. https://nodejs.org 에서 22 이상으로 올려 주세요."; Start-Process "https://nodejs.org/"; return }

# 2. OpenCode. npm은 npm.cmd로 부른다 - 그냥 npm이면 PowerShell이 npm.ps1을 골라, 실행 정책이 기본(Restricted)인
#    PC에서 "이 시스템에서 스크립트를 실행할 수 없으므로" 로 막힌다. .cmd는 실행 정책과 무관하다.
$oc = Get-Command opencode -ErrorAction SilentlyContinue
if (-not $oc) {
  Write-Host "OpenCode를 설치합니다(npm install -g opencode-ai) - 1~2분 걸립니다 ..."
  & npm.cmd install -g opencode-ai
  if ($LASTEXITCODE -ne 0) { Write-Host "OpenCode 설치에 실패했습니다. 명령 프롬프트(cmd)에서 'npm install -g opencode-ai'를 실행한 뒤 다시 시도해 주세요."; return }
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + (& npm.cmd prefix -g)
  $oc = Get-Command opencode -ErrorAction SilentlyContinue
  if (-not $oc) { Write-Host "OpenCode를 설치했지만 아직 PATH에 없습니다. PowerShell 창을 새로 열어 이 명령을 다시 붙여 넣어 주세요."; return }
}

# 3. 회사망 인증서. 사내 보안 장비가 HTTPS를 자기 인증서로 다시 서명하면, Windows는 그 루트를 믿어 irm은 되지만
#    Node.js(와 OpenCode)는 자체 CA 목록만 봐서 "self-signed certificate in certificate chain"으로 끊는다(2026-09-28 실사고).
#    Windows가 믿는 루트 인증서를 PEM으로 내보내 NODE_EXTRA_CA_CERTS로 넘긴다. 회사망이 아니어도 해가 없다.
$ca = Join-Path $base "windows-roots.pem"
(Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root | ForEach-Object {
  "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($_.RawData, "InsertLineBreaks") + "`n-----END CERTIFICATE-----"
}) | Set-Content -LiteralPath $ca -Encoding ASCII
$env:NODE_EXTRA_CA_CERTS = $ca

# 4. 컴패니언 내려받기(항상 최신으로 덮어쓴다 - 파일 하나, 의존 패키지 없음)
$mjs = Join-Path $base "mopan-code.mjs"
Invoke-WebRequest -Uri "$server/api/code/companion" -Headers @{ Authorization = "Bearer $token" } -OutFile $mjs -UseBasicParsing

# 5. 다음부터 두 번 클릭할 실행 파일. 한글 경로가 깨지지 않게 내용은 .ps1(UTF-8 BOM)에, .cmd는 ASCII 한 줄.
$ps1 = Join-Path $base "connect.ps1"
@"
`$host.UI.RawUI.WindowTitle = 'MOPAN 내 컴퓨터 연결'
Set-Location -LiteralPath '$base'
`$env:NODE_EXTRA_CA_CERTS = '$ca'
& node '$mjs' --server '$server' --token '$token' --dir '$dir'
Read-Host '연결이 끊어졌습니다. Enter를 누르면 창이 닫힙니다'
"@ | Set-Content -LiteralPath $ps1 -Encoding UTF8
$cmd = Join-Path $base "connect.cmd"
"@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0connect.ps1`"`r`n" | Set-Content -LiteralPath $cmd -Encoding ASCII

Write-Host ""
Write-Host "준비 끝. 다음부터는 이 파일을 두 번 클릭하면 바로 연결됩니다:  $cmd"
Write-Host "연결하는 폴더: $dir   (이 창을 닫으면 연결이 끊어집니다)"
Write-Host ""
& node $mjs --server $server --token $token --dir $dir
}
