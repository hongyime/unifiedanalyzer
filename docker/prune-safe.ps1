# prune-safe.ps1 — reclaim Docker space that lives in the C:-backed WSL2 vhdx,
# WITHOUT ever touching data we cannot lose.
#
# WHAT IT PRUNES (all safe — only ever recreated by a rebuild):
#   - dangling images : untagged leftover layers from `docker build`
#   - build cache     : keeps 10GB so the next rebuild is still fast; prunes rest
#
# WHAT IT NEVER TOUCHES (hard guardrails — see the explicit NON-flags below):
#   - NO --volumes  -> would delete the shared unifiedcollector_postgres data
#                      volume = the entire database. NEVER.
#   - NO -a / --all -> would remove unifiedanalyzer:latest whenever it is briefly
#                      not running, forcing a full re-pull/rebuild.
#   - NO `container prune` -> our services are restart:unless-stopped (running),
#                      and a stopped container might be intentional. Left out.
#
# Run manually:  pwsh -File C:\unifiedanalyzer\docker\prune-safe.ps1
# Scheduled:     see docker/register-prune-task.ps1 (weekly Windows task).

$ErrorActionPreference = 'Continue'
$logDir  = 'Z:\unifiedanalyzer\logs'          # keep the log off C:
$logFile = Join-Path $logDir 'docker-prune.log'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $line | Tee-Object -FilePath $logFile -Append
}

Log "=== prune-safe start ==="

# Bail out if Docker isn't up — don't error-spam the log.
docker info *> $null
if ($LASTEXITCODE -ne 0) { Log "docker not available — skipping"; exit 0 }

Log ("before: " + ((docker system df --format '{{.Type}}={{.Size}}') -join '  '))

# 1) dangling images only (NOT -a)
$img = docker image prune -f 2>&1 | Select-String 'reclaimed'
Log ("image prune: " + ($img -join ' '))

# 2) build cache, ALWAYS keeping a 10GB reserve so the next rebuild stays fast.
#    Docker 28+/buildkit renamed --keep-storage -> --reserved-space; the old
#    flag is deprecated on Docker 29 and trips the (now-removed) destructive
#    fallback below. Try the new flag first, then the old one for legacy
#    daemons. CRITICAL: we DO NOT fall back to an unreserved `builder prune` —
#    wiping the whole cache forces a ~25min cold rebuild of the heavy ML pip
#    layer (onnxruntime / insightface / faiss-cpu / scikit-learn / umap).
#    Skipping the cache prune is always preferable to nuking it; the cache is
#    self-bounding and only this project's, so letting it sit costs little.
$bc = docker builder prune -f --reserved-space 10GB 2>&1
if ($LASTEXITCODE -ne 0) {
    $bc = docker builder prune -f --keep-storage 10GB 2>&1
}
if ($LASTEXITCODE -ne 0) {
    Log "builder prune: SKIPPED (no supported reserve flag on this daemon; refusing to wipe cache)"
} else {
    Log ("builder prune: " + (($bc | Select-String 'reclaimed') -join ' '))
}

Log ("after:  " + ((docker system df --format '{{.Type}}={{.Size}}') -join '  '))
Log "=== prune-safe done ==="
