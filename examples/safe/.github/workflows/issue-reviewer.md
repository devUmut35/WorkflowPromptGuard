---
on:
  issues:
    types: [opened]
permissions:
  contents: read
  issues: read
network: defaults
tools:
  github:
    toolsets: [issues]
safe-outputs:
  add-comment:
    max: 1
---

# Issue reviewer

Summarize the issue, identify missing reproduction details, and propose a concise maintainer
response. Request an `add-comment` safe output; do not modify repository contents.
