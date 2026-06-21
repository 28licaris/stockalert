# Development launchers

Local development entrypoints that wire external development services to the
application without storing credentials in the repository.

Run the authenticated API from the repository root:

```bash
scripts/dev/run_auth_api.sh
```

The launcher reads the Cognito app-client secret from AWS at runtime using the
`stockalert-admin` profile by default. Override `AWS_PROFILE` when needed.

For a same-origin local auth flow, keep Cognito logout pointed at
`http://localhost:8000/app/login`. Using `http://localhost:5173/...` while the
authenticated app is running from FastAPI can make signout feel inconsistent
because the browser bounces between two origins.
