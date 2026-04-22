param(
    [string]$SubscriptionId = '9bc590be-f9d4-4605-bf6f-bb21a7ca21fa',
    [string]$OutputPath = '.\output\nonprivate-resources.csv'
)

$ErrorActionPreference = 'Stop'

$query = @"
Resources
| where isnotempty(resourceGroup)
| extend publicNetworkAccess = tostring(properties.publicNetworkAccess)
| extend publicNetworkAccessEnabled = tostring(properties.publicNetworkAccessEnabled)
| extend defaultAction = tostring(properties.networkAcls.defaultAction)
| extend allowPublic = iif(publicNetworkAccess =~ 'Enabled' or publicNetworkAccessEnabled =~ 'true' or defaultAction =~ 'Allow', true, false)
| where allowPublic == true
| project name, type, resourceGroup, location, publicNetworkAccess, publicNetworkAccessEnabled, defaultAction, id
| order by type asc, name asc
"@

Write-Host "Querying subscription $SubscriptionId..."

# Use az resource graph query and capture output safely (avoid piping az directly in PowerShell)
$json = az graph query -q $query --subscriptions $SubscriptionId -o json
if ($LASTEXITCODE -ne 0) {
    throw "az graph query failed with exit code $LASTEXITCODE"
}

$result = $json | ConvertFrom-Json
$data = $result.data

if (-not $data) {
    Write-Host 'No non-private resources found.'
    return
}

# Export to CSV for easy review
$data | Export-Csv -NoTypeInformation -Path $OutputPath

Write-Host "Found $($data.Count) non-private resources."
Write-Host "Saved to: $OutputPath"
Write-Host ''
$data | Format-Table -AutoSize

