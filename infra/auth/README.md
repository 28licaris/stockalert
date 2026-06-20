# Customer authentication infrastructure

`cognito.yaml` defines the development Cognito user pool used by the dashboard BFF. It enables email/password sign-in, optional TOTP MFA, account recovery by verified email, OAuth authorization-code flow, token revocation, and an optional Google identity provider.

PostgreSQL remains the source of truth for application profiles, roles, subscriptions, sessions, and audit records. Cognito stores credentials and performs authentication.

## Deploy a development pool

Choose a globally unique domain prefix and callback URLs that match the API configuration:

```bash
aws cloudformation deploy \
  --stack-name stockalert-auth-dev \
  --template-file infra/auth/cognito.yaml \
  --parameter-overrides \
    EnvironmentName=dev \
    DomainPrefix=stockalert-yourname-dev \
    CallbackUrls=http://localhost:8000/auth/callback \
    LogoutUrls=http://localhost:5173/app/login
```

The app client is confidential. Retrieve its secret after deployment and store it in a local secret file or AWS Secrets Manager; never commit it:

```bash
aws cognito-idp describe-user-pool-client \
  --user-pool-id USER_POOL_ID \
  --client-id CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' \
  --output text
```

Use the stack outputs to populate `COGNITO_ISSUER`, `COGNITO_CLIENT_ID`, `COGNITO_DOMAIN`, and the callback/logout settings documented in `.env.example`.

Google sign-in requires a Google OAuth client. Redeploy with `EnableGoogle=true`, `GoogleClientId`, and `GoogleClientSecret`. Pass the secret through a protected CI/CD secret, not a checked-in parameters file.

CI unit tests do not require AWS: JWT keys and provider responses are replaced with deterministic local fakes. A dedicated integration environment can deploy this same template and run the opt-in Cognito tests against the real pool.
