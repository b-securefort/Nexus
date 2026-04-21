# Circuit Breaker Pattern

## Overview

Use the circuit breaker pattern to prevent cascading failures when calling external services.

## Implementation

We use Polly (.NET) or resilience4j (Java) with the following defaults:

- **Failure threshold:** 5 failures in 30 seconds
- **Open duration:** 60 seconds
- **Half-open test calls:** 3

## Azure-Specific Considerations

- Apply circuit breakers around Azure Service Bus, Cosmos DB, and external API calls
- Use Azure Application Insights to track circuit state transitions
- Configure health probes on dependent services

## Example (Python with tenacity)

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def call_external_service():
    response = requests.get("https://api.example.com/data")
    response.raise_for_status()
    return response.json()
```
