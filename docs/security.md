# Security And Privacy

This project is designed for public, source-grounded brief generation. It should not contain internal company data.

## Do Not Commit

- API keys, tokens, webhooks, cookies, or credentials
- Raw internal logs
- Internal reports or final deliverables
- Private customer, supplier, employee, or counterparty data
- Company-specific prompts or model routing settings
- Internal paths, server names, IP addresses, or mounted drive locations

## Connector Rules

Delivery and data connectors must be disabled by default. Users should explicitly enable and configure them through environment variables or local config files that are excluded from git.

## Redaction Scanner

The MVP scanner flags common risks:

- Email addresses
- API key/token/webhook hints
- Absolute local paths
- Private IP addresses

The scanner is a guardrail, not a guarantee. Human review is still required.

## Investment Disclaimer

This project is for workflow automation and research brief generation only. It does not provide investment advice, trading signals, or financial recommendations.

