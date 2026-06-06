# Final Go-Live Checklist

## 1. Infrastructure Readiness

1. Cloud Run service is deployed and healthy.
2. Custom domain is mapped: https://razedevstudios.com.gigachad.
3. DNS records required by domain mapping are added and propagated.
4. HTTPS certificate is provisioned and valid.
5. Health endpoint responds: GET /health returns status ok.

## 2. Environment and Secrets

1. GOOGLE_CHAT_VERIFICATION_TOKEN is set to a strong random value.
2. CHAT_ALLOWED_USERS includes only owner and business partner.
3. CHAT_MAX_RESPONSE_CHARS is set (recommended 3000-3500).
4. OLLAMA_BASE_URL is set if local/self-hosted inference is used.
5. ANTHROPIC_API_KEY and ANTHROPIC_MODEL are set if Anthropic routes are used.
6. Secrets are stored in secure secret management (not plain text in source control).

## 3. Model and Agent Validation

1. software_dev agent model is confirmed in agents/software_dev.yaml.
2. Model is available in runtime (pull/load complete) and responds in expected latency.
3. Compression/quantization settings are validated for 24GB GPU capacity.
4. Agent prompts and task outputs are checked for production relevance.
5. Fallback behavior is defined if model/backend is temporarily unavailable.

## 4. Google Chat App Configuration

1. Google Chat API is enabled in the correct Google Cloud project.
2. App connection is set to HTTP endpoint URL.
3. Endpoint URL is set to: https://razedevstudios.com.gigachad/google-chat/events.
4. App visibility is restricted to owner + business partner for initial rollout.
5. App is installed in DM and one shared space for testing.

## 5. Security Controls

1. Webhook token check is enabled and verified.
2. Unauthorized user requests are blocked and return expected message.
3. Logs do not leak tokens, API keys, or sensitive payloads.
4. Rate limits or basic abuse controls are in place.
5. Incident contact and escalation path are documented.

## 6. Functional Acceptance Tests

1. /agents returns all expected agent IDs.
2. /ask software_dev <prompt> returns valid output.
3. Unknown agent ID returns safe error response.
4. Long output is truncated safely and clearly.
5. ADDED_TO_SPACE event returns onboarding response.
6. Invalid token request is rejected.
7. Allowed user succeeds; non-allowlisted user is blocked.

## 7. Operational Readiness

1. Centralized logging is enabled and searchable.
2. Basic alerts are configured (service down, high error rate, high latency).
3. Deployment script and rollback command are tested once.
4. On-call owner knows restart and rollback steps.
5. Known limitations are documented.

## 8. Launch and Hypercare

1. Go live with restricted audience (owner + partner) for 24-48 hours.
2. Review logs after first real interactions.
3. Capture issues, fix, and redeploy if needed.
4. Re-run acceptance tests after any hotfix.
5. Expand app visibility only after stable hypercare window.

## 9. Rollback Plan (Must Be Ready Before Launch)

1. Previous stable Cloud Run revision is identified.
2. One-command traffic rollback is documented.
3. Temporary maintenance response message is prepared.
4. Criteria for rollback are explicit (for example: sustained 5xx, auth bypass, unusable latency).

## 10. Final Launch Gate

1. All checklist items above are completed.
2. Owner sign-off completed.
3. Partner sign-off completed.
4. Production launch timestamp recorded.
5. First post-launch review scheduled.
