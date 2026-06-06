param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $false)]
    [string]$Region = 'us-central1',

    [Parameter(Mandatory = $false)]
    [string]$ServiceName = 'local-agent-chat-bridge',

    [Parameter(Mandatory = $false)]
    [string]$Domain = 'razedevstudios.com.gigachad',

    [Parameter(Mandatory = $false)]
    [string]$ChatAllowedUsers = 'you@razedevstudios.com,partner@razedevstudios.com',

    [Parameter(Mandatory = $true)]
    [string]$GoogleChatVerificationToken,

    [Parameter(Mandatory = $false)]
    [string]$OllamaBaseUrl = '',

    [Parameter(Mandatory = $false)]
    [string]$AnthropicApiKey = '',

    [Parameter(Mandatory = $false)]
    [string]$AnthropicModel = ''
)

$ErrorActionPreference = 'Stop'

Write-Host 'Setting active project...'
gcloud config set project $ProjectId | Out-Null

Write-Host 'Enabling required APIs...'
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com chat.googleapis.com | Out-Null

$image = "gcr.io/$ProjectId/$ServiceName:latest"

Write-Host 'Building container image with Cloud Build...'
gcloud builds submit --tag $image

$envVars = @(
    "GOOGLE_CHAT_VERIFICATION_TOKEN=$GoogleChatVerificationToken",
    "CHAT_ALLOWED_USERS=$ChatAllowedUsers",
    'CHAT_MAX_RESPONSE_CHARS=3500'
)
if ($OllamaBaseUrl) { $envVars += "OLLAMA_BASE_URL=$OllamaBaseUrl" }
if ($AnthropicApiKey) { $envVars += "ANTHROPIC_API_KEY=$AnthropicApiKey" }
if ($AnthropicModel) { $envVars += "ANTHROPIC_MODEL=$AnthropicModel" }

Write-Host 'Deploying Cloud Run service...'
gcloud run deploy $ServiceName `
  --image $image `
  --region $Region `
  --platform managed `
  --allow-unauthenticated `
  --port 8080 `
  --set-env-vars ($envVars -join ',')

Write-Host 'Creating domain mapping...'
gcloud run domain-mappings create --service $ServiceName --domain $Domain --region $Region

Write-Host 'Done. Next:'
Write-Host "1) Add DNS records requested by gcloud for $Domain"
Write-Host "2) In Google Chat API config, set endpoint to https://$Domain/google-chat/events"
Write-Host "3) Add app to Chat and run /agents"
