<p align="right"><strong>English</strong> · <a href="terms.zh-CN.md">简体中文</a></p>

# Accounts and terms

hey-my-buddy runs the coding harnesses you installed, on your machine, with your own sign-in. It is not a party to your agreements with harness or model vendors and does not interpret them. This page says what hey-my-buddy does with your accounts and which terms still apply to you. It is not legal advice.

## What hey-my-buddy does

- It starts the command-line program of the harness you installed (for Codex, the App Server that comes with the Codex CLI) without modifying it, and leaves the harness's sign-in, settings, providers and extensions as you configured them.
- Each harness signs in with what it stores itself. hey-my-buddy does not keep, copy or forward the credentials you sign in with. Provider keys that exist only as environment variables in your shell are not passed to Workers.
- To show whether a buddy is paid by subscription or metered, it reads only the provider and the access type from the harness's own status reply or configuration file. Keys stored in the same file are not copied or kept.
- When work can run in parallel, it runs several Workers at once, within the limits you set in the console. All usage is made with your accounts and counts against your plans and keys.
- It does not host harnesses for other people, resell usage or sign anyone in.
- Session records keep what an execution displayed, which can include sensitive text from command output. They stay in the private state on your machine, are left out of backups and exports, and are deleted with their macro task.

## Terms that still apply to you

Two layers of terms apply, and hey-my-buddy changes neither:

1. **The harness.** Each harness has its own licence and terms of use. They apply however you pay for the models.
2. **The model service**, by how you pay for it:
   - **Subscription:** the vendor's terms for your plan apply, whether the harness signs in with an account or with a key the plan issued. A plan may assume one person's ordinary use; whether several Workers running at once fit your plan is for the vendor to decide.
   - **Metered:** each request is billed to the owner of the key, under the terms of the provider that issued it.

Before raising concurrency in the console, check your plan's terms and limits. Going past them can lead to rate limits, temporary restrictions or action on the account.

## Official terms

The vendors' own pages are authoritative and change over time. They apply to a harness in every form, CLI or app.

| Harness | Pages |
| --- | --- |
| Claude Code | [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance); Anthropic's [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) and [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) |
| Codex | [Authentication](https://learn.chatgpt.com/docs/auth); OpenAI's [Terms of Use](https://openai.com/policies/terms-of-use/) |
| ZCode | Z.ai's [GLM Coding Plan Usage Policy](https://docs.z.ai/devpack/usage-policy) |
| DeepSeek Harness | DeepSeek's [Open Platform Terms of Service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) |
