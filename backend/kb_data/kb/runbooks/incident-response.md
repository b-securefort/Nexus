# Incident Response Runbook

## Severity Levels

| Level | Definition | Response Time |
|-------|-----------|---------------|
| SEV1 | Complete service outage | 15 minutes |
| SEV2 | Partial outage or degraded performance | 30 minutes |
| SEV3 | Minor issue, workaround available | 4 hours |

## Step-by-Step Response

1. **Acknowledge** the alert in PagerDuty/Azure Monitor
2. **Assess** the severity using the table above
3. **Communicate** in the #incidents Slack/Teams channel
4. **Investigate** using Azure Application Insights, Log Analytics
5. **Mitigate** — apply the quickest fix to restore service
6. **Resolve** — apply the permanent fix
7. **Post-mortem** — write an ADR documenting root cause and prevention

## Useful Azure CLI Commands

```bash
# Check resource health
az resource show --ids /subscriptions/{sub}/resourceGroups/{rg}/providers/...

# View recent activity log
az monitor activity-log list --resource-group {rg} --offset 1h

# Check App Service status
az webapp show --name {app} --resource-group {rg} --query state
```
