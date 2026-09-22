<#
.SYNOPSIS
    Deploys the Route Travel Time Tracker to Google Cloud.

.DESCRIPTION
    Creates a Cloud Storage bucket, uploads the route config, deploys a
    Cloud Function (2nd gen), and sets up Cloud Scheduler to trigger it on
    a recurring schedule.

.PARAMETER ProjectId
    Google Cloud project ID.

.PARAMETER ApiKey
    Google Maps Directions API key.

.PARAMETER Region
    GCP region for deployment (default: us-central1).

.PARAMETER BucketName
    Cloud Storage bucket name. Defaults to "<ProjectId>-route-tracker".

.PARAMETER Schedule
    Cron expression for the polling interval (default: "*/30 * * * *" = every 30 min).

.PARAMETER ConfigFile
    Path to the local config.json (default: config.json in the script directory).

.EXAMPLE
    .\deploy.ps1 -ProjectId "my-project-123" -ApiKey "AIzaSy..."
    .\deploy.ps1 -ProjectId "my-project-123" -ApiKey "AIzaSy..." -Schedule "*/15 * * * *"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [string]$ApiKey,

    [string]$Region = "us-central1",
    [string]$BucketName = "",
    [string]$Schedule = "",
    [string]$TimeZone = "",
    [string]$ConfigFile = "config.json",
    [string]$DashboardKey = ""
)

$ErrorActionPreference = "Stop"

if (-not $BucketName) {
    $BucketName = "$ProjectId-route-tracker"
}

# Auto-detect schedule and timezone from config.json if not explicitly provided
if (Test-Path $ConfigFile) {
    try {
        $configData = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($configData.schedule) {
            if (-not $Schedule -and $configData.schedule.interval_minutes) {
                $Schedule = "*/$($configData.schedule.interval_minutes) * * * *"
            }
            if (-not $TimeZone -and $configData.schedule.timezone) {
                $TimeZone = $configData.schedule.timezone
            }
        }
    } catch {
        # Fall back to defaults
    }
}

if (-not $Schedule) {
    $Schedule = "*/30 * * * *"
}
if (-not $TimeZone) {
    $TimeZone = "Asia/Kolkata"
}

$FunctionName = "route-tracker"
$SchedulerJobName = "route-tracker-schedule"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Route Tracker - Cloud Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Project:   $ProjectId"
Write-Host "  Region:    $Region"
Write-Host "  Bucket:    $BucketName"
Write-Host "  Schedule:  $Schedule"
Write-Host "  Timezone:  $TimeZone"
Write-Host "  Function:  $FunctionName"
Write-Host ""

# ---- Step 1: Set project ----
Write-Host "[1/5] Setting active project..." -ForegroundColor Yellow
gcloud config set project $ProjectId

# ---- Step 2: Create bucket (if it doesn't exist) ----
Write-Host "[2/5] Creating Cloud Storage bucket..." -ForegroundColor Yellow
$bucketExists = $false
try {
    $null = gsutil ls -b "gs://$BucketName" 2>&1
    $bucketExists = $true
} catch {
    $bucketExists = $false
}
if (-not $bucketExists) {
    gsutil mb -p $ProjectId -l $Region "gs://$BucketName"
    Write-Host "  + Bucket created: gs://$BucketName" -ForegroundColor Green
} else {
    Write-Host "  + Bucket already exists: gs://$BucketName" -ForegroundColor Green
}

# ---- Step 3: Upload config ----
Write-Host "[3/5] Uploading config.json..." -ForegroundColor Yellow
if (Test-Path $ConfigFile) {
    gsutil cp $ConfigFile "gs://$BucketName/config.json"
    Write-Host "  + Config uploaded to gs://$BucketName/config.json" -ForegroundColor Green
} else {
    Write-Host "  X Config file not found: $ConfigFile" -ForegroundColor Red
    Write-Host "    Run 'python setup_route.py' first to create it." -ForegroundColor Red
    exit 1
}

# ---- Step 4: Deploy Cloud Function (2nd gen) ----
Write-Host "[4/5] Deploying Cloud Function..." -ForegroundColor Yellow
gcloud functions deploy $FunctionName `
    --gen2 `
    --region $Region `
    --runtime python312 `
    --trigger-http `
    --allow-unauthenticated `
    --entry-point track_routes `
    --source . `
    --set-env-vars "GOOGLE_MAPS_API_KEY=$ApiKey,GCS_BUCKET=$BucketName,CONFIG_FILENAME=config.json,DASHBOARD_KEY=$DashboardKey" `
    --memory 256MB `
    --timeout 60s

$FunctionUrl = gcloud functions describe $FunctionName `
    --gen2 --region $Region --format="value(serviceConfig.uri)"
Write-Host "  + Function deployed: $FunctionUrl" -ForegroundColor Green

# ---- Step 5: Create Cloud Scheduler job ----
Write-Host "[5/5] Creating Cloud Scheduler job..." -ForegroundColor Yellow

# Delete existing job if present (idempotent re-deploys)
$existingJob = $null
try {
    $existingJob = gcloud scheduler jobs list `
        --location $Region --format="value(name)" `
        --filter="name~$SchedulerJobName" 2>&1
} catch {
    $existingJob = $null
}
if ($existingJob) {
    gcloud scheduler jobs delete $SchedulerJobName --location $Region --quiet
}

gcloud scheduler jobs create http $SchedulerJobName `
    --location $Region `
    --schedule "$Schedule" `
    --uri $FunctionUrl `
    --http-method POST `
    --time-zone "$TimeZone" `
    --attempt-deadline 120s

Write-Host "  + Scheduler job created: $SchedulerJobName ($Schedule, $TimeZone)" -ForegroundColor Green

# ---- Done ----
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Function URL:  $FunctionUrl"
Write-Host "  Dashboard:     $FunctionUrl" -ForegroundColor Cyan
Write-Host "  CSV location:  gs://$BucketName/travel_times.csv"
Write-Host ""
Write-Host "  Test tracking (POST):"
Write-Host "    curl -X POST $FunctionUrl"
Write-Host ""
Write-Host "  Open dashboard (GET):"
Write-Host "    Open $FunctionUrl in your browser or Android phone"
Write-Host ""
Write-Host "  Download logs:"
Write-Host "    gsutil cp gs://$BucketName/travel_times.csv ."
Write-Host ""
Write-Host "  Change schedule:"
Write-Host "    gcloud scheduler jobs update http $SchedulerJobName --location $Region --schedule '*/15 * * * *'"
Write-Host ""
