param(
    [string]$ModelRoot = 'Z:\unifiedanalyzer\models\ollama',
    [string]$BindAddress = '127.0.0.1:11434'
)

$ErrorActionPreference = 'Stop'
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $ollama)) { throw 'Install Ollama before starting graph inference' }
$env:OLLAMA_HOST = $BindAddress
$env:OLLAMA_MODELS = $ModelRoot
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_CONTEXT_LENGTH = '2048'
$env:OLLAMA_KEEP_ALIVE = '0'
$env:OLLAMA_NO_CLOUD = '1'
$env:CUDA_VISIBLE_DEVICES = '-1'
$env:OLLAMA_VULKAN = '0'
& $ollama serve
exit $LASTEXITCODE
