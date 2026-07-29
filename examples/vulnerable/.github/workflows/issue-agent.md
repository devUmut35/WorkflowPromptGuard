---
on:
  issues:
    types: [opened]
  roles: all
permissions:
  contents: write
max-ai-credits: -1
strict: false
tools:
  bash: [":*"]
  deploy:
    env:
      TOKEN: ${{ secrets.DEPLOY_TOKEN }}
network:
  allowed: ["*"]
safe-outputs:
  threat-detection: false
  create-pull-request:
    target-repo: "*"
---

# Unsafe issue agent

Read the issue and make any requested repository change.
