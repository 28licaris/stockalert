# Development launchers

Local development entrypoints that wire external development services to the
application without storing credentials in the repository.

Run the authenticated API from the repository root:

```bash
scripts/dev/run_auth_api.sh
```

The launcher reads the Cognito app-client secret from AWS at runtime using the
`stockalert-admin` profile by default. Override `AWS_PROFILE` when needed.
